"""Verify per-task settings and the resume/turn RPC boundary without model calls."""
import unittest
from unittest.mock import patch
import release_docs as docs
import review_runner
import test_turn_recovery as fixtures


class DocsModelTest(unittest.IsolatedAsyncioTestCase):
    async def test_doc_session_overrides_inherited_effort_without_changing_review_settings(self):
        before=(review_runner.MODEL,review_runner.EFFORT)
        async def enter(server):
            server.config={'model_reasoning_effort':review_runner.EFFORT,'features.fast_mode':False,
                           'features.multi_agent':False}
            return server
        with patch.object(docs.AppServer,'__aenter__',enter):
            server=docs.DocsServer(None)
            self.assertIs(await server.__aenter__(),server)
        self.assertEqual(docs.MODEL,'gpt-6.2-sol')
        self.assertEqual(server.config['model_reasoning_effort'],'high')
        self.assertFalse(server.config['features.fast_mode'])
        self.assertFalse(server.config['features.multi_agent'])
        self.assertEqual((review_runner.MODEL,review_runner.EFFORT),before)


class DocsModelRPCTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.GenerateRecoveryTest.asyncSetUp
    context=fixtures.GenerateRecoveryTest.context
    send=fixtures.GenerateRecoveryTest.send

    async def test_resume_and_turn_use_requested_model_and_level(self):
        await docs.generate(self.config,self.state,self.run)
        calls=dict(self.calls)
        self.assertEqual(calls['thread/resume']['model'],'gpt-6.2-sol')
        self.assertIsNone(calls['thread/resume']['serviceTier'])
        self.assertEqual(calls['turn/start']['model'],'gpt-6.2-sol')
        self.assertEqual(calls['turn/start']['effort'],'high')
        self.assertIsNone(calls['turn/start']['serviceTier'])
        self.assertEqual(self.run['thread_id'],self.thread)

if __name__=='__main__':unittest.main()
