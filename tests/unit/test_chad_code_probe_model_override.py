"""Exercise model selection through the real CLI main and dispatch config boundary."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from evals import chad_code_e2e_probe as probe

class TestProbeModelOverride(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, explicit=None, fail=False, no_codegraph=False):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            observed=[]
            async def dispatch(**kwargs):
                actual=probe.ohd._cfg('openhands_coder_model',probe.ohd._DEFAULT_MODEL)
                observed.append((actual,probe.ohd._cfg('openhands_codegraph_mcp',True)))
                if fail: raise RuntimeError('dispatch error')
                return {'success':True,'result':{'job_id':'model','container':'fake','workspace':temp,'model':actual}}
            args=['probe','--task','offline','--poll','0']
            if explicit: args+=['--model',explicit]
            if no_codegraph: args+=['--no-codegraph']
            cfg=lambda key,default=None:'openai/configured' if key=='openhands_coder_model' else default
            old_default=probe.ohd._DEFAULT_MODEL
            with patch('sys.argv',args),patch.object(probe.ohd,'_cfg',cfg),patch.object(probe.OpenHandsDispatchTool,'execute',side_effect=dispatch),patch.object(probe.CheckCodingTaskTool,'execute',AsyncMock(return_value={'success':True,'result':{'status':'finished','exit_code':'0','files':['file']}})),patch.object(probe,'_full_logs',return_value='done'),patch.object(probe,'_report_dir',return_value=root),patch.object(probe,'run_provenance',return_value={}),contextlib.redirect_stdout(io.StringIO()):
                try:
                    if fail:
                        with self.assertRaisesRegex(RuntimeError,'dispatch error'): await probe.main()
                    else:
                        self.assertEqual(await probe.main(),0)
                        report=json.loads((root/'model.json').read_text())
                        self.assertEqual(report['model'],explicit or 'openai/configured')
                    self.assertIs(probe.ohd._cfg,cfg)
                    self.assertEqual(probe.ohd._DEFAULT_MODEL,old_default)
                finally:
                    probe.ohd._DEFAULT_MODEL=old_default
                    probe.ohd._cfg=cfg
            self.assertEqual(observed[0][0],explicit or 'openai/configured')
            self.assertEqual(observed[0][1],not no_codegraph)
    async def test_explicit_then_default_preserve_configuration(self):
        await self.invoke('openai/explicit',no_codegraph=True)
        await self.invoke()
    async def test_dispatch_exception_restores_configuration(self):
        await self.invoke('openai/explicit',fail=True,no_codegraph=True)
