"""Failure recovery boundaries; no real model, checkout or GitHub publication."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
import pr_recovery as recovery
import queue_state as queue
import review_runner as rr


class RecoveryTests(unittest.TestCase):
    def entry(self,**updates):
        return {'snapshot':{'head':'a'},'status':'error','phase':None,'attempts':queue.MAX_ATTEMPTS,
                'ready_at':100,'queued_at':1,**updates}

    def test_recovery_is_once_per_head_after_budget_and_quiet_period(self):
        e=self.entry()
        self.assertFalse(recovery.eligible(e,queue.MAX_ATTEMPTS,99))
        self.assertTrue(recovery.eligible(e,queue.MAX_ATTEMPTS,100))
        for updates in [{'retry_blocked':True},{'recovery_head':'a'},{'attempts':1},{'phase':'active'},{'status':'processed'}]:
            self.assertFalse(recovery.eligible({**e,**updates},queue.MAX_ATTEMPTS,100))
        self.assertTrue(recovery.eligible({**e,'recovery_head':'old'},queue.MAX_ATTEMPTS,100))

    def test_recovery_fifo(self):
        state={'entries':{'2':self.entry(queued_at=2),'1':self.entry(queued_at=1)}}
        self.assertEqual(recovery.candidates(state,queue.MAX_ATTEMPTS,100),['1','2'])

    def test_immediate_diagnosis_is_only_git_preparation(self):
        self.assertTrue(recovery.git_preparation_error(RuntimeError('git failed: corrupt object')))
        self.assertTrue(recovery.git_preparation_error(RuntimeError('trusted pro submodule source changed')))
        self.assertFalse(recovery.git_preparation_error(RuntimeError('LLM timeout')))
        self.assertFalse(recovery.git_preparation_error(RuntimeError('cyberPolicy')))

    def test_diagnosis_receipt_prevents_second_paid_turn(self):
        with tempfile.TemporaryDirectory()as temp:
            folder=Path(temp)
            (folder/'recovery.json').write_text('{"status":"starting"}')
            with patch.object(rr,'require_reviewable'),patch.object(rr,'review_job',new_callable=AsyncMock)as model:
                with self.assertRaises(RuntimeError):
                    asyncio.run(recovery.execute({'folder':temp,'head_sha':'a'}, {},'git failed'))
                model.assert_not_awaited()

    def test_unknown_diagnosis_is_not_published_or_repaired(self):
        with tempfile.TemporaryDirectory()as temp:
            job={'folder':temp,'head_sha':'a'}
            async def model(job,state):
                (Path(temp)/'recovery-decision.json').write_text(json.dumps(
                    {'head':'a','cause':'unknown','evidence':['network cause unconfirmed']}))
            with patch.object(rr,'require_reviewable'),patch.object(recovery,'evidence',return_value={}), \
                 patch.object(rr,'review_job',side_effect=model),patch.object(rr,'published_review')as publish:
                with self.assertRaises(RuntimeError):
                    asyncio.run(recovery.execute(job,{},'git failed'))
                publish.assert_not_called()
            self.assertEqual(json.loads((Path(temp)/'recovery.json').read_text())['status'],'failed')

    def test_local_repair_needs_validation_before_normal_review(self):
        with tempfile.TemporaryDirectory()as temp:
            job={'folder':temp,'head_sha':'a'}
            async def model(job,state):
                (Path(temp)/'recovery-decision.json').write_text(json.dumps(
                    {'head':'a','cause':'local','evidence':['stale own lock'],'repaired':True,
                     'validation':['verified owner exited and own Git reads succeed']}))
            with patch.object(rr,'require_reviewable'),patch.object(recovery,'evidence',return_value={}), \
                 patch.object(rr,'review_job',side_effect=model):
                state={}
                self.assertEqual(asyncio.run(recovery.execute(job,state,'git failed')),'local')
                self.assertEqual(state['status'],'recovery_repaired')


if __name__=='__main__':
    unittest.main()
