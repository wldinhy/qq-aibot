import asyncio
import unittest

from qq_aibot.queue import RequestQueue


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tasks = set()

    async def asyncTearDown(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def test_burst_keeps_every_message_in_session_order(self):
        results = []

        async def handler(job):
            await asyncio.sleep(0)
            results.append(job)

        queue = RequestQueue(handler, 4, self.tasks)
        for number in range(50):
            queue.submit('A', number)
        await asyncio.wait_for(queue.ready.join(), 2)
        self.assertEqual(results, list(range(50)))

    async def test_other_conversation_runs_while_first_is_waiting(self):
        release = asyncio.Event()
        other_done = asyncio.Event()
        results = []

        async def handler(job):
            if job == 'A1':
                await release.wait()
            results.append(job)
            if job == 'B1':
                other_done.set()

        queue = RequestQueue(handler, 2, self.tasks)
        for session, job in [('A', 'A1'), ('A', 'A2'), ('A', 'A3'), ('B', 'B1')]:
            queue.submit(session, job)
        await asyncio.wait_for(other_done.wait(), 1)
        self.assertEqual(results, ['B1'])
        release.set()
        await asyncio.wait_for(queue.ready.join(), 1)
        self.assertEqual(results, ['B1', 'A1', 'A2', 'A3'])

    async def test_serial_mode_is_global_fifo(self):
        results = []

        async def handler(job):
            results.append(job)

        queue = RequestQueue(handler, 1, self.tasks)
        for session, job in [('A', 'A1'), ('A', 'A2'), ('B', 'B1'), ('A', 'A3')]:
            queue.submit(session, job)
        await asyncio.wait_for(queue.ready.join(), 1)
        self.assertEqual(results, ['A1', 'A2', 'B1', 'A3'])

    async def test_failure_does_not_stop_following_requests(self):
        results = []

        async def handler(job):
            if job == 'bad':
                raise RuntimeError('test')
            results.append(job)

        queue = RequestQueue(handler, 2, self.tasks)
        queue.submit('A', 'bad')
        queue.submit('A', 'good')
        await asyncio.wait_for(queue.ready.join(), 1)
        self.assertEqual(results, ['good'])
