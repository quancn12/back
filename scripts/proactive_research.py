"""Local research readiness, conformance and deterministic D0 controller smoke."""
import argparse
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.proactive.controller import initial_state, change_mode, poll, acknowledge, reply
from app.services.proactive.manifest import readiness, load_manifest, digest
from app.services.proactive.experiments import VARIANTS, IMPLEMENTED_VARIANTS, score


def smoke(question=False):
    state = initial_state()
    assert poll(state,1000,True,True)[0]['status']=='OFF'
    change_mode(state,'LIVE',1000)
    state['fixture']={'origin':1000.,'question':question}
    result,decision=poll(state,1000,True,True)
    event=result['event'];assert event is not None
    assert poll(state,1000,True,True)[0]['event']['id']==event['id']
    acknowledge(state,event['id'],1000,True,True)
    if question:
        linked,_=reply(state,event['transaction_id'],'yes','reply-1',1001,True,True)
        assert linked['event']['kind']=='OFFER'
        acknowledge(state,linked['event']['id'],1001,True,True)
    change_mode(state,'OFF',1002)
    assert poll(state,1003,True,True)[0]['event'] is None
    return {'source_kind':'D0-MATH','status':'PASS','decision':decision,
            'delivered_messages':state['ledger'],'empirical_benefit_claim':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['readiness','smoke','validate-implementation','validate-manifest','score'])
    parser.add_argument('--manifest');parser.add_argument('--predictions');parser.add_argument('--references');parser.add_argument('--output')
    args=parser.parse_args()
    if args.command=='validate-implementation':
        return subprocess.call([sys.executable,'-B','-m','unittest','discover','-s','tests','-p','test_proactive*.py','-v'],cwd=ROOT)
    if args.command=='readiness':
        result={**readiness(),'variants':{v:'IMPLEMENTED' if v in IMPLEMENTED_VARIANTS else 'BLOCKED_SOURCE_OR_REFIT_ASSETS' for v in VARIANTS}}
    elif args.command=='smoke': result={'direct':smoke(),'question':smoke(True)}
    elif args.command=='validate-manifest':
        if not args.manifest:parser.error('--manifest is required')
        raw,cfg,model=load_manifest(args.manifest)
        result={'status':'VALID_ARTIFACT','hash':cfg.config_hash,'source_kind':raw['source_kind'],'live_authorized':False}
    else:
        if not args.predictions or not args.references:parser.error('--predictions and --references required')
        result=score(json.loads(Path(args.predictions).read_text()),json.loads(Path(args.references).read_text()))
    report={'runtime':{'python':sys.version,'os':platform.platform(),'machine':platform.machine(),'timezone':'UTC',
                       'timestamp':time.time(),'code_hash':digest({str(p.relative_to(ROOT)):p.read_text(encoding='utf-8') for p in sorted((ROOT/'app/services/proactive').glob('*.py'))})},'result':result}
    text=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)
    if args.output:
        output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(text,encoding='utf-8')
    print(text)
    return 0


if __name__=='__main__':raise SystemExit(main())
