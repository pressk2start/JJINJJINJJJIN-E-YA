import csv
import tempfile
import unittest
from pathlib import Path
from net_edge_oos import load, evaluate, _quantile


class NetEdgeTest(unittest.TestCase):
    def fixture(self, improve_holdout=False):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        opp = Path(d.name) / 'opportunities.csv'
        fills = Path(d.name) / 'events.csv'
        with opp.open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['event_id','date','market','decision_recv_ms','buy_imbalance'])
            w.writeheader()
            for day in range(1,11):
                for k in range(20):
                    w.writerow(dict(event_id=f'{day}:{k}',date=f'2026-10-{day:02d}',
                                    market='KRW-A' if k%2 else 'KRW-B',
                                    decision_recv_ms=day*86400000+k*60000,
                                    buy_imbalance=k/19))
        with fills.open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['event_id','date','market','horizon_s','entry_fill_ms','exit_fill_ms','net_return_frac'])
            w.writeheader()
            for day in range(1,11):
                for k in range(20):
                    val = .003 if k>=15 else -.002
                    if improve_holdout and day>=8:
                        val = .50  # absurdly good OOS must NOT move TRAIN threshold
                    w.writerow(dict(event_id=f'{day}:{k}', date=f'2026-10-{day:02d}',
                                    market='KRW-A' if k%2 else 'KRW-B',
                                    horizon_s=10,entry_fill_ms=day*86400000+k*60000+200,
                                    exit_fill_ms=day*86400000+k*60000+10300,net_return_frac=val))
        return opp, fills

    def test_train_only_threshold_unaffected_by_holdout_outcome(self):
        a,b=self.fixture(); x,y=load(a,b)
        report1=evaluate(x,y,min_train=10,min_test=10)
        a,b=self.fixture(improve_holdout=True); x,y=load(a,b)
        report2=evaluate(x,y,min_train=10,min_test=10)
        self.assertEqual(report1['train_only_quantile_thresholds'],report2['train_only_quantile_thresholds'])
        self.assertEqual(report1['selected_by_train'],report2['selected_by_train'])
        self.assertNotEqual(report1['test_all_events_baseline']['mean_net_pct'],report2['test_all_events_baseline']['mean_net_pct'])

    def test_per_trade_conversion_once_and_fee_not_deducted_again(self):
        a,b=self.fixture(); x,y=load(a,b)
        self.assertAlmostEqual(y[('1:19',10)],.3)
        m=evaluate(x,y,min_train=10,min_test=10)['test_all_events_baseline']
        self.assertAlmostEqual(m['mean_stress_pct'],m['mean_net_pct']-0.05)

    def test_duplicate_outcome_rejected(self):
        a,b=self.fixture()
        with b.open('r') as f: lines=f.readlines()
        with b.open('a') as f:f.write(lines[1])
        with self.assertRaisesRegex(ValueError,'duplicate fill'):
            load(a,b)

    def test_missing_opportunities_rejected(self):
        a,b=self.fixture()
        with a.open('r') as f: lines=f.readlines()
        with a.open('w') as f:f.writelines(lines[:-1])
        with self.assertRaisesRegex(ValueError,'without recorded opportunity'):
            load(a,b)

    def test_negative_and_nonfinite_rejected(self):
        a,b=self.fixture()
        lines=b.read_text().splitlines()
        lines[1]=lines[1].rsplit(',',1)[0]+',NaN'
        b.write_text('\n'.join(lines)+'\n')
        with self.assertRaisesRegex(ValueError,'nonfinite'):
            load(a,b)

    def test_early_exit_lookahead_rejected(self):
        a,b=self.fixture()
        lines=b.read_text().splitlines()
        parts=lines[1].split(',');parts[5]=str(int(parts[4])+9000)
        lines[1]=','.join(parts)
        b.write_text('\n'.join(lines)+'\n')
        with self.assertRaisesRegex(ValueError,'before prescribed hold'):
            load(a,b)

    def test_coverage_cannot_fake_proven(self):
        a,b=self.fixture();x,y=load(a,b)
        y={k:v for k,v in y.items() if x[k[0]]['date'] < '2026-10-08' or k[0]=='8:19'}
        r=evaluate(x,y,min_train=10,min_test=1)
        self.assertNotEqual(r['status'],'HISTORICAL_CANDIDATE')

    def test_quantile(self):
        self.assertEqual(_quantile([1,2,3],0.5),2)
        self.assertEqual(_quantile([1,2,3,4],0.5),2.5)


if __name__=='__main__':
    unittest.main()
