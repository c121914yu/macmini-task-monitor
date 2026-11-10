"""Calendar projection uses isolated monitor SQLite; it never starts a business task."""
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import calendar_api
import store
from config import ZONE


class CalendarTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        for key,value in [('DATA',root),('DB_PATH',root/'monitor.sqlite3')]:
            p=patch.object(store,key,value);p.start();self.addCleanup(p.stop)
        store.initialize()
        today=dt.datetime.now(ZONE).date()
        self.monday=today-dt.timedelta(days=today.weekday())

    def task(self,id,rules=None,interval=None):
        task={'id':id,'name':id,'category':'work','loaded':True,'schedule':{'type':'interval','seconds':interval} if interval else {'type':'calendar','rules':rules}}
        store.save_task(task)
        return task

    def record(self,id,task,at,**kwargs):
        store.insert_run({'id':id,'task_id':task,'external_key':id,'kind':'execution','status':'success',
                          'created_at':at,'started_at':at,'finished_at':at+10,**kwargs})

    def test_daily_weekly_rules_and_duplicate_rules_use_shanghai(self):
        self.task('daily',[{'Hour':7,'Minute':0},{'Hour':7,'Minute':0}])
        self.task('weekly',[{'Weekday':5,'Hour':10,'Minute':0}])
        data=calendar_api.week_snapshot(self.monday.isoformat())
        events=[e for day in data['days'].values() for e in day['planned']]
        self.assertEqual(len(events),8)
        friday=self.monday+dt.timedelta(days=4)
        self.assertEqual(sum(e['task_id']=='weekly' for e in data['days'][friday.isoformat()]['planned']),1)
        first=data['days'][self.monday.isoformat()]['planned'][0]['at']
        self.assertEqual(dt.datetime.fromtimestamp(first,dt.timezone.utc).hour,23)
        self.assertEqual(dt.datetime.fromtimestamp(first,ZONE).date(),self.monday)

    def test_frequent_rules_are_counted_in_separate_lane_without_skips(self):
        self.task('interval',interval=300)
        self.task('hourly',[{'Minute':20}])
        at=dt.datetime.combine(self.monday,dt.time(7),ZONE).timestamp()
        self.record('a','interval',at)
        self.record('b','interval',at+300,status='failed')
        self.record('skip','interval',at+600,status='skipped')
        data=calendar_api.week_snapshot(self.monday.isoformat())
        self.assertEqual(data['recurring_ids'],['hourly','interval'])
        day=data['days'][self.monday.isoformat()]
        self.assertEqual(day['planned'],[])
        self.assertEqual(day['actual'],[])
        self.assertEqual(day['recurring']['interval']['count'],2)
        self.assertEqual(day['recurring']['interval']['last_run_id'],'b')
        self.assertEqual(day['recurring']['interval']['statuses'],{'success':1,'failed':1})

    def test_unknown_start_and_exclusive_week_end_preserve_evidence(self):
        self.task('daily',[{'Hour':7,'Minute':0}])
        at=dt.datetime.combine(self.monday,dt.time(),ZONE).timestamp()
        self.record('unknown','daily',at,started_at=None,finished_at=None,kind='history')
        self.record('outside','daily',at+7*86400)
        data=calendar_api.week_snapshot(self.monday.isoformat())
        events=[e for day in data['days'].values() for e in day['actual']]
        self.assertEqual(len(events),1)
        self.assertFalse(events[0]['time_known'])
        self.assertIsNone(events[0]['duration'])
        self.assertNotIn('log_tail',events[0])

    def test_invalid_rules_dates_and_month_filters_do_not_invent_events(self):
        task=self.task('monthly',[{'Month':2,'Day':29,'Hour':1,'Minute':30}])
        self.assertEqual(len(calendar_api.planned_times(task,dt.date(2028,2,29))),1)
        self.assertEqual(calendar_api.planned_times(task,dt.date(2027,2,28)),[])
        task['schedule']['rules']=[{'Hour':25,'Minute':0},{'Unknown':1}]
        self.assertEqual(calendar_api.planned_times(task,self.monday),[])
        for value in ['invalid','2026-02-30','2000-01-01']:
            with self.assertRaises(ValueError):calendar_api.week_snapshot(value)


if __name__=='__main__':unittest.main()
