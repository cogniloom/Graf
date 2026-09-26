import unittest
from lawcase import parser

class CliTests(unittest.TestCase):
    def test_mcp_json_argv_preserves_flags_and_spaces(self):
        args=parser().parse_args(['init','/source path','--mcp-command','["haiku-rag", "--read-only", "mcp", "--stdio"]'])
        self.assertEqual(args.mcp_command,['haiku-rag','--read-only','mcp','--stdio'])
    def test_research_resume_requires_no_repeated_issue(self):
        args=parser().parse_args(['research','--resume','.lawcase/authorities/test'])
        self.assertIsNone(args.public_issue)
        self.assertEqual(str(args.resume),'.lawcase/authorities/test')
