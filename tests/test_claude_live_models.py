"""Claude models belong to a process/session and survive long compaction records."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsmon import detect


def assistant(model, **extra):
    return json.dumps({'type': 'assistant', 'message': {'model': model}, **extra}) + '\n'


class ClaudeLiveModels(unittest.TestCase):
    def test_compaction_does_not_erase_the_last_real_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            path.write_text(assistant('claude-opus-5') + assistant('claude-fable-5-1') +
                            json.dumps({'type': 'system', 'subtype': 'compact_boundary', 'content': 'x' * 200000}) + '\n')
            self.assertEqual(detect._claude_model_from_transcript(str(path)), 'Fable 5.1')

    def test_models_in_tool_payloads_and_sidechains_do_not_replace_the_main_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            path.write_text(assistant('claude-fable-5-1') +
                            json.dumps({'type': 'user', 'message': {'model': 'claude-opus-5'}}) + '\n' +
                            assistant('claude-opus-5', isSidechain=True) + assistant('<synthetic>'))
            self.assertEqual(detect._claude_model_from_transcript(str(path)), 'Fable 5.1')

    def test_process_registry_keeps_claude_sessions_with_shared_cwd_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            sessions = home / '.claude/sessions'
            sessions.mkdir(parents=True)
            projects = home / '.claude/projects/-project'
            projects.mkdir(parents=True)
            ids = ('11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222')
            for pid, sid, model in zip((10, 20), ids, ('claude-fable-5-1', 'claude-opus-5')):
                (sessions / f'{pid}.json').write_text(json.dumps({'pid': pid, 'sessionId': sid, 'cwd': '/project'}))
                (projects / f'{sid}.jsonl').write_text(assistant(model))
            with patch.object(Path, 'home', return_value=home), \
                 patch.object(detect, '_proc_table', return_value=({10: 'claude', 20: 'claude'}, {})), \
                 patch.object(detect, 'tmux_sessions', return_value=[{'name': 'A', 'created': 1}, {'name': 'B', 'created': 1}]), \
                 patch.object(detect, '_pane_pids', side_effect=lambda name: [10 if name == 'A' else 20]), \
                 patch.object(detect, '_session_cwd', return_value='/project'):
                rows = detect.discover_agents()
            self.assertEqual([(r['session_id'], r['label']) for r in rows],
                             [(ids[0], 'Fable 5.1'), (ids[1], 'Opus 5')])

    def test_explicit_resume_id_never_borrows_a_newer_siblings_model(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            project = home / '.claude/projects/-project'
            project.mkdir(parents=True)
            own_id = '11111111-1111-1111-1111-111111111111'
            (project / f'{own_id}.jsonl').write_text(assistant('claude-fable-5-1'))
            (project / '22222222-2222-2222-2222-222222222222.jsonl').write_text(assistant('claude-opus-5'))
            with patch.object(Path, 'home', return_value=home):
                self.assertEqual(detect._claude_info_for_processes([10], '/project', own_id),
                                 (own_id, 'Fable 5.1'))

    def test_unverified_identity_does_not_guess_from_a_shared_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            sessions = home / '.claude/sessions'
            sessions.mkdir(parents=True)
            (sessions / '10.json').write_text(json.dumps({'pid': 99, 'sessionId': 'foreign', 'cwd': '/project'}))
            with patch.object(Path, 'home', return_value=home):
                self.assertEqual(detect._claude_info_for_processes([10], '/project'), (None, None))


if __name__ == '__main__':
    unittest.main()
