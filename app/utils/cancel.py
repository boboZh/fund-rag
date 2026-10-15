import asyncio
import threading
import time

from starlette.concurrency import run_in_threadpool


class Cancelled(Exception):
    pass


def throw_if_cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled("检索已取消")


# 重试前的等待。请求断了就立刻返回，不再发下一次请求
def wait_or_cancel(seconds, cancel):
    if cancel is None:
        time.sleep(seconds)
        return
    # 重试前的等待，会阻塞当前线程，直到下面两种情况之一发生：
    # 1. 其它线程调用了cancel.set(): 如watch()检测到客户端断开连接后会调用它。这时wait马上返回True，不再等满seconds秒
    # 2. 等满seconds秒，标志仍然是False：wait返回False
    if cancel.wait(seconds):
        raise Cancelled("检索已取消")


# threading.Event相当于Node的signal
# set()对应abort()
# is_set()对应signal.aborted
async def run_until_disconnect(request, fn, **kwargs):
    """
    入参：FastAPI 的 request、同步的服务函数 fn 和它的参数。
    出参：fn 的返回值。
    步骤：fn 放进线程池跑；同时每 0.5 秒问一次浏览器还在不在,断开就拨动开关；fn 结束后停止询问。
    """
    cancel = threading.Event()

    async def watch():
        while True:
            if await request.is_disconnected():
                cancel.set()
                return
            await asyncio.sleep(0.5)

    watcher = asyncio.create_task(watch())
    try:
        return await run_in_threadpool(fn, cancel=cancel, **kwargs)
    finally:
        watcher.cancel()
