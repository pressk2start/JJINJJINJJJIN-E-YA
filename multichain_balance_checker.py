# -*- coding: utf-8 -*-
"""
multichain_balance_checker.py  v4.0  — API 키 없이 동작
대화형: 체인 선택 → 주소 입력 → native + token 잔고 조회
소수점 끝자리까지 정확히 보존
지원 (17종):
  BTC          mempool.space (무료, 키 불필요)
  LTC          Trezor Blockbook (무료)
  BCH          Zelcore Blockbook (무료)
  RVN          Ravencoin Blockbook (무료)
  ETH          공개 RPC + eth_call balanceOf (무료)
  BNB          공개 RPC + eth_call balanceOf (무료)
  POL          공개 RPC + eth_call balanceOf (무료)
  KAIA         공개 RPC + Kaiascan (무료)
  SGB          공개 RPC (무료)
  XRP          공식 Ripple RPC (무료)
  XLM          Horizon API (무료)
  SOL          공개 RPC (무료)
  TRX          TronGrid (무료, 키 있으면 rate limit ↑)
  ONT          공식 노드 (무료)
  ARDR         Jelurida 공개 노드 (무료)
  EOS          Hyperion (무료)
  PCI          scan.payprotocol.io (무료, 비공식)
선택 환경변수 (없어도 동작, 있으면 rate limit 완화):
  TRONGRID_API_KEY, KAIASCAN_API_KEY, SOL_RPC_URL, XRP_RPC_URL, ARDOR_NODE_URL
"""
from __future__ import annotations
import os, sys, json, requests
from decimal import Decimal, InvalidOperation
from typing import Any, List, Optional
TIMEOUT = 20
S = requests.Session()
S.headers.update({"User-Agent": "multichain-balance-checker/4.0"})
# ═══════════════════════════════════════════════
#  공통 유틸
# ═══════════════════════════════════════════════
def http_get(url, headers=None, params=None) -> Any:
    r = S.get(url, headers=headers or {}, params=params or {}, timeout=TIMEOUT)
    r.raise_for_status()
    ct = r.headers.get("content-type", "")
    return r.json() if ("application/json" in ct or r.text[:1] in ("{", "[")) else r.text
def http_post(url, payload, headers=None) -> Any:
    r = S.post(url, json=payload, headers=headers or {}, timeout=TIMEOUT)
    r.raise_for_status()
    ct = r.headers.get("content-type", "")
    return r.json() if ("application/json" in ct or r.text[:1] in ("{", "[")) else r.text
def hex2int(h: str) -> str:
    return str(int(h, 16))
def fmt(raw: str | int, dec: int) -> str:
    """base-unit 정수 → 정확한 소수 문자열"""
    s = str(raw).strip()
    neg = s.startswith("-")
    if neg: s = s[1:]
    if not s.isdigit():
        raise ValueError(f"정수가 아님: {raw}")
    if dec == 0:
        out = s
    else:
        s = s.zfill(dec + 1)
        out = f"{s[:-dec]}.{s[-dec:]}"
    return f"-{out}" if neg else out
def noexp(v: str) -> str:
    t = str(v).strip()
    if not t: return "0"
    if "e" not in t.lower(): return t
    try: return format(Decimal(t), "f")
    except InvalidOperation: return t
def R(chain, addr, ticker, amount, raw=None, dec=None, ct=None, typ="token", **kw):
    d = dict(chain=chain, address=addr, ticker=ticker, amount=amount,
             raw_amount=raw, decimals=dec, contract=ct, type=typ)
    d.update(kw)
    return d
# ═══════════════════════════════════════════════
#  BTC  — mempool.space (키 불필요)
# ═══════════════════════════════════════════════
def get_btc(addr):
    d = http_get(f"https://mempool.space/api/address/{addr}")
    cs = d["chain_stats"]
    ms = d["mempool_stats"]
    funded = cs["funded_txo_sum"] + ms["funded_txo_sum"]
    spent  = cs["spent_txo_sum"]  + ms["spent_txo_sum"]
    raw = str(funded - spent)
    return [R("BTC", addr, "BTC", fmt(raw, 8), raw, 8, None, "native")]
# ═══════════════════════════════════════════════
#  LTC  — Trezor Blockbook (키 불필요)
# ═══════════════════════════════════════════════
def get_ltc(addr):
    d = http_get(f"https://ltc1.trezor.io/api/v2/address/{addr}?details=basic")
    raw = str(d.get("balance", "0"))
    return [R("LTC", addr, "LTC", fmt(raw, 8), raw, 8, None, "native")]
# ═══════════════════════════════════════════════
#  BCH  — Zelcore Blockbook (키 불필요)
# ═══════════════════════════════════════════════
def get_bch(addr):
    d = http_get(f"https://blockbook.bch.zelcore.io/api/v2/address/{addr}?details=basic")
    raw = str(d.get("balance", "0"))
    return [R("BCH", addr, "BCH", fmt(raw, 8), raw, 8, None, "native")]
# ═══════════════════════════════════════════════
#  RVN  — Ravencoin Blockbook (키 불필요)
# ═══════════════════════════════════════════════
def get_rvn(addr):
    try:
        d = http_get(f"https://blockbook.ravencoin.org/api/v2/address/{addr}?details=basic")
        raw = str(d.get("balance", "0"))
        return [R("RVN", addr, "RVN", fmt(raw, 8), raw, 8, None, "native")]
    except Exception:
        bal = str(http_get(f"https://chainz.cryptoid.info/rvn/api.dws?q=getbalance&a={addr}")).strip()
        return [R("RVN", addr, "RVN", noexp(bal), None, 8, None, "native")]
# ═══════════════════════════════════════════════
#  EVM 공통: 공개 RPC (ETH / BNB / POL)
#  native = eth_getBalance
#  token  = Ankr 무료 getAccountBalance (multi-token 한방 조회)
# ═══════════════════════════════════════════════
EVM_CFG = {
    "ETH": {
        "rpc": "https://ethereum-rpc.publicnode.com",
        "ankr": "eth",
        "dec": 18,
    },
    "BNB": {
        "rpc": "https://bsc-dataseed.bnbchain.org",
        "ankr": "bsc",
        "dec": 18,
    },
    "POL": {
        "rpc": "https://polygon-bor-rpc.publicnode.com",
        "ankr": "polygon",
        "dec": 18,
    },
}
def _evm_rpc(rpc_url, method, params):
    return http_post(rpc_url, {
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params
    })
def get_evm_balances(chain, addr):
    cfg = EVM_CFG[chain]
    # native
    resp = _evm_rpc(cfg["rpc"], "eth_getBalance", [addr, "latest"])
    raw = hex2int(resp["result"])
    results = [R(chain, addr, chain, fmt(raw, cfg["dec"]), raw, cfg["dec"], None, "native")]
    # tokens via Ankr 무료 Advanced API (키 불필요)
    try:
        ankr_resp = http_post("https://rpc.ankr.com/multichain", {
            "jsonrpc": "2.0", "id": 1,
            "method": "ankr_getAccountBalance",
            "params": {
                "walletAddress": addr,
                "blockchain": [cfg["ankr"]],
                "onlyWhitelisted": False,
                "pageSize": 50,
            }
        })
        for asset in ankr_resp.get("result", {}).get("assets", []):
            sym = asset.get("tokenSymbol") or asset.get("tokenName") or "UNKNOWN"
            ct = asset.get("contractAddress") or ""
            dec = int(asset.get("tokenDecimals", 0) or 0)
            # native는 위에서 이미 추가했으므로 skip
            if asset.get("tokenType", "").upper() == "NATIVE":
                continue
            # balanceRawInteger로 정확한 자릿수 보존
            raw_int = str(asset.get("balanceRawInteger", "")).strip()
            if raw_int and raw_int.isdigit() and raw_int != "0":
                results.append(R(chain, addr, sym, fmt(raw_int, dec),
                                 raw_int, dec, ct))
            else:
                # fallback: 이미 변환된 balance 문자열
                bal_str = asset.get("balance", "0")
                if bal_str in ("0", "0.0", ""):
                    continue
                results.append(R(chain, addr, sym, noexp(bal_str), None, dec, ct))
    except Exception:
        pass  # Ankr 실패 시 native만
    return results
# ═══════════════════════════════════════════════
#  KAIA  — 공개 RPC + Kaiascan (키 없어도 동작)
# ═══════════════════════════════════════════════
KAIA_RPC = "https://public-en.node.kaia.io"
def get_kaia(addr):
    rpc = os.getenv("KAIA_RPC_URL", KAIA_RPC)
    resp = _evm_rpc(rpc, "eth_getBalance", [addr, "latest"])
    raw = hex2int(resp["result"])
    results = [R("KAIA", addr, "KAIA", fmt(raw, 18), raw, 18, None, "native")]
    # tokens
    hdr = {"accept": "application/json"}
    kk = os.getenv("KAIASCAN_API_KEY", "").strip()
    if kk:
        hdr["x-api-key"] = kk
    page = 1
    while True:
        try:
            data = http_get(
                "https://mainnet-oapi.kaiascan.io/api/v1/accounts/fungible-token-balances",
                headers=hdr,
                params={"accountAddress": addr, "page": page, "size": 100}
            )
        except Exception:
            break
        items = (data.get("result", {}).get("items")
                 or data.get("result", {}).get("results")
                 or data.get("items") or data.get("results") or [])
        if not items:
            break
        for it in items:
            r = str(it.get("balance") or it.get("token_balance") or "0")
            if r == "0": continue
            sym = it.get("token_symbol") or it.get("symbol") or "UNKNOWN"
            d = it.get("token_decimal") or it.get("decimals")
            ct = it.get("contract_address") or it.get("token_address")
            if d is None:
                amt, dv = r, None
            else:
                dv = int(d)
                amt = fmt(r, dv)
            results.append(R("KAIA", addr, sym, amt, r, dv, ct))
        if len(items) < 100: break
        page += 1
    return results
# ═══════════════════════════════════════════════
#  SGB (Songbird) — 공개 RPC
# ═══════════════════════════════════════════════
def get_sgb(addr):
    resp = _evm_rpc("https://songbird-api.flare.network/ext/C/rpc",
                    "eth_getBalance", [addr, "latest"])
    raw = hex2int(resp["result"])
    return [R("SGB", addr, "SGB", fmt(raw, 18), raw, 18, None, "native")]
# ═══════════════════════════════════════════════
#  XRP  — 공식 Ripple RPC (키 불필요)
# ═══════════════════════════════════════════════
def get_xrp(addr):
    rpc = os.getenv("XRP_RPC_URL", "https://s1.ripple.com:51234/")
    info = http_post(rpc, {"method": "account_info",
                           "params": [{"account": addr, "ledger_index": "validated"}]})
    lines = http_post(rpc, {"method": "account_lines",
                            "params": [{"account": addr, "ledger_index": "validated"}]})
    raw_drops = str(info["result"]["account_data"]["Balance"])
    results = [R("XRP", addr, "XRP", fmt(raw_drops, 6), raw_drops, 6, None, "native")]
    for ln in lines["result"].get("lines", []):
        bal = noexp(str(ln["balance"]))
        cur = ln.get("currency", "UNKNOWN")
        issuer = ln.get("account")
        if bal not in ("0", "0.0", "0.000000", ""):
            results.append(R("XRP", addr, cur, bal, None, None, issuer,
                             "token", issuer=issuer))
    return results
# ═══════════════════════════════════════════════
#  XLM  — Horizon API (키 불필요)
# ═══════════════════════════════════════════════
def get_xlm(addr):
    data = http_get(f"https://horizon.stellar.org/accounts/{addr}")
    results = []
    for b in data.get("balances", []):
        bal = noexp(str(b.get("balance", "0")))
        if b.get("asset_type") == "native":
            results.append(R("XLM", addr, "XLM", bal, None, 7, None, "native"))
        else:
            code = b.get("asset_code", "UNKNOWN")
            iss = b.get("asset_issuer")
            results.append(R("XLM", addr, code, bal, None, None, iss,
                             "token", issuer=iss))
    return results
# ═══════════════════════════════════════════════
#  SOL  — 공개 RPC (키 불필요)
# ═══════════════════════════════════════════════
def _sol(method, params):
    rpc = os.getenv("SOL_RPC_URL", "https://api.mainnet-beta.solana.com")
    return http_post(rpc, {"jsonrpc": "2.0", "id": 1,
                           "method": method, "params": params})
def get_sol(addr):
    native = _sol("getBalance", [addr])
    tokens = _sol("getTokenAccountsByOwner", [
        addr,
        {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
        {"encoding": "jsonParsed"}
    ])
    raw_l = str(native["result"]["value"])
    results = [R("SOL", addr, "SOL", fmt(raw_l, 9), raw_l, 9, None, "native")]
    for it in tokens["result"]["value"]:
        info = it["account"]["data"]["parsed"]["info"]
        ta = info["tokenAmount"]
        raw = str(ta["amount"])
        if raw == "0": continue
        dec = int(ta["decimals"])
        mint = info["mint"]
        results.append(R("SOL", addr, mint, fmt(raw, dec), raw, dec, mint))
    return results
# ═══════════════════════════════════════════════
#  TRX  — TronGrid (키 없어도 동작, 있으면 rate limit ↑)
# ═══════════════════════════════════════════════
def _trx_hdr():
    h = {}
    k = os.getenv("TRONGRID_API_KEY", "").strip()
    if k: h["TRON-PRO-API-KEY"] = k
    return h
def get_trx(addr):
    hdr = _trx_hdr()
    acct = http_post("https://api.trongrid.io/wallet/getaccount",
                     {"address": addr, "visible": True}, headers=hdr)
    raw_sun = str(acct.get("balance", 0))
    results = [R("TRX", addr, "TRX", fmt(raw_sun, 6), raw_sun, 6, None, "native")]
    # TRC20
    try:
        data = http_get(f"https://api.trongrid.io/v1/accounts/{addr}", headers=hdr)
        for token_map in data.get("data", [{}])[0].get("trc20", []):
            for ca, rb in token_map.items():
                rs = str(rb)
                if rs == "0": continue
                try:
                    ci = http_get(f"https://api.trongrid.io/v1/contracts/{ca}", headers=hdr)
                    cd = ci.get("data", [{}])[0]
                    sym = cd.get("symbol") or cd.get("name") or ca[:8]
                    dec = int(cd.get("decimals", 0) or 0)
                except Exception:
                    sym, dec = ca[:8], 0
                results.append(R("TRX", addr, sym, fmt(rs, dec), rs, dec, ca))
    except Exception:
        pass
    return results
# ═══════════════════════════════════════════════
#  ONT  — 공식 노드 (키 불필요)
#  ONT: decimals=9, ONG: decimals=18 (balancev2는 raw 최소단위 반환)
# ═══════════════════════════════════════════════
def get_ont(addr):
    data = http_get(f"https://dappnode1.ont.io:20334/api/v1/balancev2/{addr}")
    result = data.get("Result") or data.get("result") or {}
    results = []
    # balancev2는 raw 최소단위 반환: ONT=9 decimals, ONG=18 decimals
    if "ont" in result:
        raw_ont = str(result["ont"])
        results.append(R("ONT", addr, "ONT", fmt(raw_ont, 9), raw_ont, 9, None, "native"))
    if "ong" in result:
        raw_ong = str(result["ong"])
        results.append(R("ONT", addr, "ONG", fmt(raw_ong, 18), raw_ong, 18, None, "token"))
    return results
# ═══════════════════════════════════════════════
#  ARDR  — Jelurida 공개 노드 (키 불필요)
# ═══════════════════════════════════════════════
def _ardor(params):
    node = os.getenv("ARDOR_NODE_URL", "https://ardor.jelurida.com/nxt")
    return http_get(node, params=params)
def get_ardr(addr):
    native = _ardor({"requestType": "getBalance", "chain": "1", "account": addr})
    assets = _ardor({"requestType": "getAccountAssets",
                     "account": addr, "includeAssetInfo": "true"})
    raw_nqt = str(native.get("balanceNQT", "0"))
    results = [R("ARDR", addr, "ARDR", fmt(raw_nqt, 8), raw_nqt, 8, None, "native")]
    for a in assets.get("accountAssets", []):
        rq = str(a.get("quantityQNT", "0"))
        if rq == "0": continue
        d = int(a.get("decimals", 0) or 0)
        sym = a.get("name") or a.get("asset") or "ASSET"
        results.append(R("ARDR", addr, sym, fmt(rq, d), rq, d, a.get("asset")))
    return results
# ═══════════════════════════════════════════════
#  EOS  — Hyperion (공개 엔드포인트 기본 내장)
# ═══════════════════════════════════════════════
def get_eos(addr):
    hyperion = os.getenv("EOS_HYPERION_URL", "https://eos.hyperion.eosrio.io")
    url = f"{hyperion.rstrip('/')}/v2/state/get_tokens"
    data = http_get(url, params={"account": addr})
    tokens = data.get("tokens") if isinstance(data, dict) else None
    if tokens is None:
        tokens = data.get("results") if isinstance(data, dict) else None
    if tokens is None and isinstance(data, list):
        tokens = data
    if not tokens:
        tokens = []
    results = []
    for it in tokens:
        af = it.get("amount") or it.get("currency") or it.get("balance")
        ct = it.get("contract") or it.get("account") or it.get("code")
        if isinstance(af, str) and " " in af:
            amt, sym = af.split(" ", 1)
        else:
            sym = it.get("symbol", "UNKNOWN")
            amt = str(it.get("balance", "0"))
        amt = noexp(amt)
        dec = len(amt.split(".")[1]) if "." in amt else 0
        results.append(R("EOS", addr, sym, amt, None, dec, ct,
                         "native" if sym == "EOS" else "token"))
    return results
# ═══════════════════════════════════════════════
#  PCI (Paycoin) — scan.payprotocol.io
# ═══════════════════════════════════════════════
def get_pci(addr):
    try:
        data = http_get(f"https://scan.payprotocol.io/api/account/{addr}")
        if isinstance(data, dict):
            return [R("PCI", addr, "PCI", noexp(str(data.get("balance", "0"))),
                       None, None, None, "native")]
    except Exception:
        pass
    raise RuntimeError(
        "PCI 잔고 조회 실패 — scan.payprotocol.io에서 직접 확인하세요")
# ═══════════════════════════════════════════════
#  체인 목록
# ═══════════════════════════════════════════════
CHAINS = {
    "BTC":  ("Bitcoin",       get_btc),
    "LTC":  ("Litecoin",      get_ltc),
    "BCH":  ("Bitcoin Cash",  get_bch),
    "RVN":  ("Ravencoin",     get_rvn),
    "ETH":  ("Ethereum",      lambda a: get_evm_balances("ETH", a)),
    "BNB":  ("BNB Chain",     lambda a: get_evm_balances("BNB", a)),
    "POL":  ("Polygon",       lambda a: get_evm_balances("POL", a)),
    "KAIA": ("Kaia",          get_kaia),
    "SGB":  ("Songbird",      get_sgb),
    "XRP":  ("XRP Ledger",    get_xrp),
    "XLM":  ("Stellar",       get_xlm),
    "SOL":  ("Solana",        get_sol),
    "TRX":  ("Tron",          get_trx),
    "ONT":  ("Ontology",      get_ont),
    "ARDR": ("Ardor",         get_ardr),
    "EOS":  ("EOS",           get_eos),
    "PCI":  ("Paycoin",       get_pci),
}
def get_balances(chain: str, address: str) -> dict:
    chain = chain.upper().strip()
    if chain not in CHAINS:
        raise ValueError(f"미지원 체인: {chain}")
    name, fn = CHAINS[chain]
    return {"chain": chain, "name": name, "address": address, "assets": fn(address)}
# ═══════════════════════════════════════════════
#  대화형 CLI
# ═══════════════════════════════════════════════
def print_assets(result):
    chain = result["chain"]
    assets = result.get("assets", [])
    print(f"\n{'='*60}")
    print(f"  {chain} ({result.get('name','')})  |  {result['address']}")
    print(f"{'='*60}")
    if not assets:
        print("  (잔고 없음)")
        return
    mx = min(max(len(a["ticker"]) for a in assets), 20)
    for a in assets:
        tk = a["ticker"]
        if len(tk) > 20:
            tk = tk[:8] + "..." + tk[-6:]
        tag = "[native]" if a["type"] == "native" else "[token] "
        print(f"  {tag}  {tk:>{mx}s}  {a['amount']}")
    print()
def interactive():
    cl = list(CHAINS.keys())
    while True:
        print("\n┌─ 지원 체인 (API 키 불필요) ──────────────────┐")
        for i, ck in enumerate(cl, 1):
            print(f"│  {i:2d}. {ck:<6s} ({CHAINS[ck][0]})")
        print("│   0. 종료")
        print("└─────────────────────────────────────────────┘")
        sel = input("체인 번호 선택: ").strip()
        if sel == "0" or sel.lower() in ("q", "quit", "exit"):
            break
        if sel.isdigit():
            idx = int(sel) - 1
            if not (0 <= idx < len(cl)):
                print("잘못된 번호"); continue
            chain = cl[idx]
        elif sel.upper() in CHAINS:
            chain = sel.upper()
        else:
            print("잘못된 입력"); continue
        address = input(f"{chain} 주소 입력: ").strip()
        if not address:
            print("주소 비어있음"); continue
        try:
            print_assets(get_balances(chain, address))
        except Exception as e:
            print(f"\n  [오류] {e}\n")
        if input("계속? (Enter=계속 / q=종료): ").strip().lower() in ("q", "quit", "exit"):
            break
if __name__ == "__main__":
    if len(sys.argv) == 3:
        try:
            print(json.dumps(get_balances(sys.argv[1], sys.argv[2]),
                             ensure_ascii=False, indent=2))
        except Exception as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False, indent=2))
    else:
        interactive()
