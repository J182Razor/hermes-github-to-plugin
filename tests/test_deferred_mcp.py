import json
import os
from pathlib import Path
import tempfile
import unittest

from generators import mcp_safe


class DeferredMCPTests(unittest.TestCase):
    def test_conversion_does_not_start_source_and_exposes_untrusted_proxy(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            source = root / 'src'
            source.mkdir()
            marker = root / 'executed'
            (source / 'server.py').write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n')
            (source / 'mcp.json').write_text(json.dumps({'mcpServers': {'fixture': {'command': 'python', 'args': ['server.py']}}}))
            output = Path(mcp_safe.generate(source, root / 'out', 'fixture', {'type': 'mcp'})['output_path'])
            self.assertFalse(marker.exists())
            config = json.loads((output / 'mcp.json').read_text())
            plugin = json.loads((output / 'plugin.json').read_text())
            name, server = next(iter(config['mcpServers'].items()))
            self.assertEqual(plugin['extensions']['com.nousresearch.hermes']['servers'][name]['trust'], 'untrusted')
            self.assertNotIn('source', server['args'][0])
