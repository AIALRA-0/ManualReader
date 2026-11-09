"""检查在其他线程触发的回收也计入请求等待，并且只计算与请求相交的时间。"""
from collections import deque
from unittest.mock import patch
import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import reader_performance as perf

class GlobalGCTiming(unittest.TestCase):
    def test_global_collection_overlaps_proxy_wait_and_handler(self):
        with patch.object(perf,'_gc_events',deque([(2.,4.),(6.,9.),(11.,12.)])),patch.object(perf.time,'perf_counter',return_value=10.):
            timing=perf.server_timing(5.,3.)
            self.assertIn('gc_pause;dur=4000.00',timing)
            self.assertIn('gc_pause;dur=3000.00',perf.server_timing(5.))
            self.assertTrue(timing.startswith('app;dur='))

if __name__=='__main__':unittest.main()
