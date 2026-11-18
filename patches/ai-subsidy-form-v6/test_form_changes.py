import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rules import Hold,check_definition,read_form,precheck
from runtime import extract,atomic_json
from notify import payload_for
from test_rules import POLICY,TODAY,form,facts


class FormChangesTest(unittest.TestCase):
    def fields(self):
        values={**form(),'official_price':'20 USD'}
        result=[]
        for key,spec in POLICY['fields'].items():
            result.append({'id':spec['id'],'name':spec['name'],'type':spec['type'],
                           'open_ids':values.get(key) if key=='person' else [],'value':values.get(key)})
        return result

    def definition(self,fields):
        return {'approval_name':POLICY['approval_name'],'form':fields}

    def test_current_definition_and_new_instance_pass(self):
        fields=self.fields();check_definition(self.definition(fields),POLICY)
        parsed=read_form({'form':json.dumps(fields)},POLICY)
        self.assertEqual(parsed['official_price'],'20 USD')
        self.assertEqual(precheck(parsed,POLICY,TODAY),[])

    def test_legacy_labels_and_missing_new_field_remain_readable(self):
        fields=self.fields()
        fields=[f for f in fields if f['id']!=POLICY['fields']['official_price']['id']]
        for field in fields:
            spec=next(s for s in POLICY['fields'].values() if s['id']==field['id'])
            field['name']=spec.get('aliases',[spec['name']])[0]
        parsed=read_form({'form':fields},POLICY)
        self.assertIsNone(parsed['official_price'])
        self.assertEqual(precheck(parsed,POLICY,TODAY),[])

    def test_new_price_widget_empty_is_a_missing_requirement(self):
        parsed=read_form({'form':self.fields()},POLICY);parsed['official_price']=''
        self.assertEqual(precheck(parsed,POLICY,TODAY),['缺少官网标明的本次订阅价格（带单位）'])

    def test_unrecognized_name_type_or_id_still_stops_definition_check(self):
        for change in [{'name':'unexpected'},{'type':'number'},{'id':'recreated-widget'}]:
            fields=self.fields();target=next(f for f in fields if f['id']==POLICY['fields']['cycle']['id']);target.update(change)
            with self.subTest(change=change),self.assertRaises(Hold):check_definition(self.definition(fields),POLICY)
            if 'id' not in change:
                with self.assertRaises(Hold):read_form({'form':fields},POLICY)

    def test_early_failure_card_does_not_claim_zero_pending(self):
        payload=payload_for({'mode':'apply','items':[],'errors':[{'reason':'definition changed'}]})
        text=json.dumps(payload,ensure_ascii=False)
        self.assertIn('本轮尚未进入待办审核',text);self.assertNotIn('待核验 0 笔',text)

    def test_official_price_is_context_and_invalidates_extraction_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'runs').mkdir()
            for name in ['evidence.schema.json','extract-prompt.txt','policy.json']:
                (root/name).write_bytes((Path(__file__).resolve().parents[1]/name).read_bytes())
            inputs=[]
            def run(args,**kwargs):
                inputs.append(kwargs['input'])
                atomic_json(Path(args[args.index('--output-last-message')+1]),facts())
                return SimpleNamespace(returncode=0)
            with patch('runtime.subprocess.run',side_effect=run):
                f={**form(),'official_price':'20 USD'};extract(f,[],root);extract(f,[],root)
                extract({**f,'official_price':'200 USD'},[],root)
            self.assertEqual(len(inputs),2)
            self.assertIn('"listed_subscription_price": "20 USD"',inputs[0])

if __name__=='__main__':unittest.main()
