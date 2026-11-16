import threading

import erp_widgets as EW


def test_excel_jobs_run_in_order_on_one_thread_that_stays():
    w = EW._ExcelWorker()
    seen, done = [], threading.Event()

    def job(i):
        seen.append((i, threading.get_ident()))
        if i == 2:
            done.set()
    for i in range(3):
        w.submit(lambda i=i: job(i))
    assert done.wait(5)
    assert [i for i, _ in seen] == [0, 1, 2]
    assert len({t for _, t in seen}) == 1 and seen[0][1] != threading.get_ident()
    assert w.t.is_alive()                      # 线程不退出（退出时拆 COM 会和界面互相卡死）


def test_failing_job_does_not_stop_worker():
    w = EW._ExcelWorker()
    ok = threading.Event()
    w.submit(lambda: 1 / 0)
    w.submit(ok.set)
    assert ok.wait(5)
