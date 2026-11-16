import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import check

DF='Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vda3 40901312 12029340 26981604 31% /\n'

class DiskCheckTest(unittest.TestCase):
    def test_df_units_and_invalid_results(self):
        self.assertEqual(check.parse_df(DF)['available_bytes'],26981604*1024)
        for invalid in ['',DF+'another line\n',DF.replace('26981604','-1'),DF.replace('26981604','90000000'),DF.replace('31% /','31% /other')]:
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):check.parse_df(invalid)

    def test_notification_is_sent_once_and_uses_shared_delivery(self):
        with tempfile.TemporaryDirectory() as t,patch.object(check,'ROOT',Path(t)),patch.object(check.delivery,'send') as send:
            config={'webhook':'https://example.invalid/never-used','threshold_bytes':10_000_000_000}
            sample={'available_bytes':9_000_000_000,'total_bytes':40_000_000_000,'checked_at':'fixture'}
            def sent(url,payload,**kwargs):kwargs['on_attempt']({'attempt':1,'status':'sent'})
            send.side_effect=sent
            self.assertEqual(check.notify(config,sample,'2026-11-17'),'sent')
            self.assertEqual(check.notify(config,sample,'2026-11-17'),'sent')
            send.assert_called_once()

    def test_interrupted_rate_limit_preserves_message_and_budget(self):
        with tempfile.TemporaryDirectory() as t,patch.object(check,'ROOT',Path(t)),patch.object(check.delivery,'send') as send:
            config={'webhook':'https://example.invalid/never-used','threshold_bytes':10_000_000_000}
            import hashlib
            original={'msg_type':'fixture'}
            check.save(Path(t)/'notifications/2026-11-17.json',{'status':'retry_wait','endpoint_sha256':hashlib.sha256(config['webhook'].encode()).hexdigest(),'payload':original,'attempts':[{'attempt':2,'status':'retry_wait','next_attempt_at':123}]})
            send.side_effect=lambda *args,**kwargs:kwargs['on_attempt']({'attempt':3,'status':'sent'})
            check.notify(config,{},'2026-11-17')
            self.assertEqual(send.call_args.args[1],original)
            self.assertEqual(send.call_args.kwargs['initial_attempts'],2)
            self.assertEqual(send.call_args.kwargs['not_before'],123)

    def test_unknown_outcome_is_not_retried(self):
        with tempfile.TemporaryDirectory() as t,patch.object(check,'ROOT',Path(t)),patch.object(check.delivery,'send') as send:
            check.save(Path(t)/'notifications/2026-11-17.json',{'status':'unknown'})
            self.assertEqual(check.notify({}, {},'2026-11-17'),'unknown');send.assert_not_called()

    def test_healthy_and_exact_threshold_do_not_notify(self):
        for available in [10_000_000_000,20_000_000_000]:
            with tempfile.TemporaryDirectory() as t,patch.object(check,'ROOT',Path(t)),patch.object(check.sys,'argv',['check.py']),patch.object(check,'collect',return_value={'available_bytes':available}),patch.object(check,'notify') as notify:
                check.save(Path(t)/'config.json',{'threshold_bytes':10_000_000_000})
                self.assertEqual(check.main(),0);notify.assert_not_called()

    def test_check_only_low_capacity_does_not_notify(self):
        with tempfile.TemporaryDirectory() as t,patch.object(check,'ROOT',Path(t)),patch.object(check.sys,'argv',['check.py','--check-only']),patch.object(check,'collect',return_value={'available_bytes':1}),patch.object(check,'notify') as notify:
            check.save(Path(t)/'config.json',{'threshold_bytes':10_000_000_000})
            self.assertEqual(check.main(),0);notify.assert_not_called()

    def test_ssh_failure_does_not_send_alert(self):
        with tempfile.TemporaryDirectory() as t,patch.object(check,'ROOT',Path(t)),patch.object(check.sys,'argv',['check.py']),patch.object(check,'collect',side_effect=RuntimeError('fixture')),patch.object(check,'notify') as notify:
            check.save(Path(t)/'config.json',{'threshold_bytes':10_000_000_000})
            self.assertEqual(check.main(),1);notify.assert_not_called()

if __name__=='__main__':unittest.main()
