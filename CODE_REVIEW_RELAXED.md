# 완화된 코드 리뷰 보고서 (Relaxed Code Review)

**파일**: `251130-MADEIT에서 여러수정보완한 찐찐찐이야야` (7,604 lines)
**리뷰어**: Claude Code
**날짜**: 2026-02-03

---

## 1. 구조적 이슈 (Structural Issues)

### 1.1 단일 파일 과부하 - HIGH
- **위치**: 전체 파일
- **내용**: 7,600+ 줄이 단일 파일에 집중되어 있음
- **권장**: config, utils, indicators, trading, telegram 등 모듈 분리

### 1.2 함수 과대화 - MEDIUM
| 함수명 | 라인 수 | 권장 |
|--------|---------|------|
| `monitor_position()` | ~600줄 | 100줄 이하로 분리 |
| `detect_leader_stock()` | ~400줄 | 로직별 분리 |
| `main()` | ~250줄 | 스캔/진입/모니터 분리 |
| `stage1_gate()` | ~200줄 | 게이트별 분리 |

### 1.3 전역 상태 과다 사용 - MEDIUM
- **위치**: Line 80~500
- **내용**: 50+ 전역 변수 (`OPEN_POSITIONS`, `_TICKS_CACHE`, `last_signal_at` 등)
- **권장**: 클래스 기반 상태 관리 또는 Context 객체 도입

---

## 2. 잠재적 버그 (Potential Bugs)

### 2.1 예외 무시 패턴 - HIGH
```python
# Line 1417, 2903, 6594 등 다수
except Exception:
    pass
```
- **문제**: 에러 원인 추적 불가, 침묵 실패
- **권장**: 최소 로깅 추가 또는 특정 예외만 캐치

### 2.2 None 체크 불완전 - MEDIUM
```python
# Line 7368-7372
cv_val = ia_stats.get("cv")
if cv_val is None:
    cv_val = 0.0  # 🔧 FIX 주석이 있지만 다른 곳에도 동일 패턴 필요
```
- **유사 위치**: `pstd_val`, `atr` 반환값 등
- **권장**: 헬퍼 함수 `safe_get(dict, key, default)` 통일 사용

### 2.3 정수 나눗셈 위험 - LOW
```python
# Line 6016
ret_pct = ((last_price / entry_price - 1.0) - FEE_RATE) * 100.0
```
- **문제**: `entry_price=0` 방어가 있지만 분산되어 있음
- **권장**: 계산 로직을 `safe_pct_change(a, b)` 헬퍼로 통일

### 2.4 딕셔너리 키 누락 가능성 - MEDIUM
```python
# Line 6098
if ret_pct >= 1.5 and t15["krw_per_sec"] < 15000:
```
- **문제**: `t15.get("krw_per_sec", 0)` 대신 직접 접근
- **권장**: `.get()` 메서드 일관 사용

### 2.5 시간 기반 로직 레이스 컨디션 - MEDIUM
```python
# Line 6257-6264 (오더북 캐시)
_ob_snap_age = time.time() - monitor_position._ob_snap_ts.get(m, 0)
if _ob_snap_age >= 10:
    # 캐시 갱신
```
- **문제**: 함수 속성을 캐시로 사용 (스레드 안전성 불확실)
- **권장**: `threading.local()` 또는 전용 캐시 클래스 사용

---

## 3. 코드 스타일 이슈 (Style Issues)

### 3.1 매직 넘버 산재 - HIGH
```python
# 예시들 (수십 개 존재)
if gain_now >= 0.012 and strong_flow and mae_check > -0.0035:  # Line 6430
PLATEAU_SEC = 20  # Line 6615
if alive_sec >= 25 and mfe_now < 0.0008  # Line 6282
```
- **권장**: 상단 상수로 통합, 문서화

### 3.2 주석 불일치 - LOW
- 한글/영어 주석 혼용
- 일부 주석이 코드와 불일치 (예: 비활성화된 코드의 주석)
- `# 🔧 FIX:` 태그가 200+ 개 존재 → 정리 필요

### 3.3 데드 코드 (주석 처리된 코드) - MEDIUM
```python
# Line 6369-6402
# === 🔧 실패돌파 - 비활성화 (진입 타이트하므로 청산은 루즈하게) ===
# BREAKOUT_THRESHOLD = 0.0015  # +0.15%
# ...
```
- **위치**: 6369-6402, 6383-6402, 기타 다수
- **권장**: VCS 히스토리에 의존, 코드에서 제거

### 3.4 라인 길이 초과 - LOW
- 80자 초과 라인 다수 (특히 f-string 알림 메시지)
- **예**: Line 7385-7395 (텔레그램 메시지 조립)

### 3.5 불필요한 변수 할당 - LOW
```python
# Line 6537
in_breakeven_box = abs(gain_from_entry) <= BREAKEVEN_BOX  # 다른 곳에서 사용이라고 주석
```
- **문제**: 선언 후 사용처가 분산되어 가독성 저하

---

## 4. 보안 고려사항 (Security Considerations)

### 4.1 API 키 노출 가능성 - MEDIUM
```python
# Line 100~110 (추정)
TG_TOKEN = os.getenv("TG_TOKEN", "")
UPBIT_ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY", "")
```
- **상태**: 환경변수 사용 (양호)
- **권장**: `.env` 파일 템플릿 제공, git ignore 확인

### 4.2 하드코딩된 경로 - LOW
```python
# Line 7089
with open("/home/ubuntu/bot/health.log", "w") as hf:
```
- **권장**: 설정 파일 또는 환경변수로 이동

### 4.3 HTTP vs HTTPS - LOW
- Upbit API는 HTTPS 사용 (양호)
- Telegram API도 HTTPS 사용 (양호)

---

## 5. 성능 이슈 (Performance Issues)

### 5.1 중복 API 호출 가능성 - MEDIUM
```python
# Line 7412-7436
c5 = get_minutes_candles(5, m, 2) or []
c15 = get_minutes_candles(15, m, 2) or []
```
- **문제**: 로깅 목적으로 추가 캔들 조회
- **권장**: 필수 아닌 경우 조건부 조회 또는 배치 처리

### 5.2 LRU 캐시 사이즈 미지정 - LOW
```python
# Line ~800 (추정)
@lru_cache(maxsize=None)  # 또는 고정값
```
- **문제**: 무제한 캐시는 메모리 누수 가능
- **권장**: 적절한 maxsize 설정

### 5.3 ThreadPoolExecutor 재사용 - GOOD (이미 수정됨)
```python
# Line 7083
_candle_executor = ThreadPoolExecutor(max_workers=PARALLEL_WORKERS)
```
- **상태**: 루프 밖에서 1회 생성 (양호)

### 5.4 문자열 연결 비효율 - LOW
```python
# Line 7234-7241
killer_vals = " ".join([...])  # 리스트 컴프리헨션 사용 (양호)
```
- 대부분 f-string 사용 (양호)

---

## 6. 에러 핸들링 (Error Handling)

### 6.1 포괄적 예외 처리 - HIGH
```python
# Line 7574
except Exception as e:
    print(f"[SYMBOL_ERR][{m}] {e}")
```
- **문제**: 모든 예외를 동일하게 처리
- **권장**: 예외 타입별 분기 (NetworkError, APIError, ValidationError 등)

### 6.2 재시도 로직 불일치 - MEDIUM
```python
# Line 6750
for _ in range(retry + 1):
```
- **문제**: 일부 함수만 재시도 로직 보유
- **권장**: `@retry` 데코레이터 통일 사용

### 6.3 로깅 레벨 미사용 - MEDIUM
```python
print("[MAIN_ERR]", e)  # Line 7593
```
- **권장**: `logging` 모듈 도입 (DEBUG/INFO/WARNING/ERROR 레벨)

---

## 7. 테스트 가능성 (Testability)

### 7.1 의존성 주입 부재 - MEDIUM
- API 클라이언트, 텔레그램 클라이언트가 전역으로 하드코딩
- 모킹 어려움 → 단위 테스트 작성 곤란
- **권장**: 클래스 초기화 시 주입받도록 리팩터링

### 7.2 사이드 이펙트가 있는 함수 - MEDIUM
```python
def open_auto_position(m, pre, dyn_stop, eff_sl_pct):
    # 전역 OPEN_POSITIONS 수정
    # 텔레그램 발송
    # API 호출
```
- **권장**: 순수 함수와 사이드 이펙트 함수 분리

---

## 8. 문서화 이슈 (Documentation)

### 8.1 Docstring 불완전 - LOW
```python
def _end_reco(entry_price, last_price, c1, ticks, ob_depth_krw, ctx_thr=3):
    """
    끝알람용 권고 생성:
      - 수익/손실, 컨텍스트, 테이프 흐름 종합으로
        유지 / 부분청산 / 전량청산 세 가지 액션 제안
    """
```
- **문제**: 파라미터 타입/반환값 설명 없음
- **권장**: Google/NumPy 스타일 docstring 통일

### 8.2 타입 힌트 부재 - LOW
```python
def monitor_position(m, entry_price, pre, tight_mode=False, horizon=None, reentry=False):
```
- **권장**:
```python
def monitor_position(
    m: str,
    entry_price: float,
    pre: Dict[str, Any],
    tight_mode: bool = False,
    ...
) -> Tuple[str, Optional[str], ...]:
```

---

## 9. 비즈니스 로직 이슈 (Business Logic)

### 9.1 하드코딩된 임계값 - MEDIUM
| 상수 | 값 | 위치 |
|------|-----|------|
| `K_VOL_BASE` | 100,000,000 | Line 7184 |
| `K_BUY` | 0.70 | Line 7186 |
| `PLATEAU_SEC` | 20 | Line 6615 |
| `NO_PEAK_TIMEOUT_SEC` | 45 | Line 6198 |

- **권장**: 설정 파일 또는 자동학습 파라미터로 이동

### 9.2 조건 복잡도 - MEDIUM
```python
# Line 6430
if gain_now >= 0.012 and strong_flow and mae_check > -0.0035:
```
- **권장**: 조건을 명명된 변수로 분리
```python
is_profitable = gain_now >= MIN_PROFIT_FOR_CONFIRM
is_stable = mae_check > MAX_MAE_FOR_CONFIRM
if is_profitable and strong_flow and is_stable:
```

### 9.3 중첩 조건문 - HIGH
```python
# Line 6277-6328 (probe 스크래치)
if entry_mode == "probe":
    if alive_sec_now >= 25 and mfe_now < 0.0008 and cur_gain_check <= 0:
        ...
    if alive_sec_now >= 60 and mfe_now < 0.0015 and cur_gain_check <= 0:
        ...
if entry_mode in ("half", "confirm") and not pre.get("mega_ok") and not pre.get("ign_ok"):
    if entry_mode == "half":
        ...
    else:
        ...
    if alive_sec_now >= _es_timeout and mfe_now < _es_mfe_thr and cur_gain_check <= _es_gain_thr:
        ...
```
- **권장**: 전략 패턴 또는 테이블 기반 분기로 단순화

---

## 10. 기타 개선 사항 (Miscellaneous)

### 10.1 Import 정리 필요 - LOW
- `from http.server import ...` (Line 6913)이 함수 정의 중간에 위치
- **권장**: 파일 상단으로 이동

### 10.2 사용되지 않는 변수 가능성 - LOW
```python
# Line 7406 이후
sent = tg_send(txt, retry=2)  # sent가 False일 때 후속 처리 있음 (양호)
```

### 10.3 클로저 변수 캡처 주의 - MEDIUM
```python
# Line 7520-7521
pre_copy = dict(pre)  # 클로저 문제 방지
def _run_monitor(market, entry, pre_data, tight):
```
- **상태**: 이미 처리됨 (양호)

### 10.4 time.sleep 하드코딩 - LOW
```python
time.sleep(3)   # Line 6237
time.sleep(0.25 + rnd() * 0.25)  # Line 6788
```
- **권장**: 상수로 정의하여 튜닝 용이하게

---

## 요약 (Summary)

| 심각도 | 개수 | 주요 항목 |
|--------|------|----------|
| HIGH | 6 | 예외 무시, 매직넘버, 포괄적 예외처리, 중첩조건, 단일파일 과부하, 함수 과대화 |
| MEDIUM | 15 | None 체크, 레이스컨디션, 데드코드, API 중복호출, 로깅 미사용 등 |
| LOW | 12 | 라인길이, 타입힌트, Docstring, Import 정리 등 |

### 즉시 수정 권장 (Quick Wins)
1. 데드 코드 제거 (주석 처리된 비활성화 로직)
2. `except Exception: pass` → 최소 로깅 추가
3. 매직 넘버 → 상단 상수로 통합
4. `.get()` 일관 사용으로 KeyError 방지

### 중기 리팩터링 권장
1. 모듈 분리 (config, trading, telegram, indicators)
2. `logging` 모듈 도입
3. 클래스 기반 상태 관리
4. 타입 힌트 추가

---

*Generated by Claude Code - Relaxed Review Mode*
