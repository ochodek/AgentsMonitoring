"""A model switch must follow the owning agent, never a sibling or old turn."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsmon import detect, wizard


def record(model):
    return json.dumps({'type': 'turn_context', 'payload': {'model': model}}) + '\n'


class LiveModels(unittest.TestCase):
    def test_model_switch_after_long_history_wins_over_initial_model_and_tool_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rollout.jsonl'
            path.write_text(record('gpt-5.5') + '{}\n' * 100 + record('gpt-6-astra') +
                            json.dumps({'type': 'response_item', 'payload': {'model': 'gpt-5.6'}}) + '\n')
            self.assertEqual(detect._rollout_model(str(path)), 'GPT-6 Astra')

    def test_latest_model_survives_a_large_record_at_end_of_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rollout.jsonl'
            path.write_text(record('gpt-6-astra') + json.dumps({'type': 'response_item', 'text': 'x' * 200000}) + '\n')
            self.assertEqual(detect._rollout_model(str(path)), 'GPT-6 Astra')

    def test_agents_sharing_a_directory_keep_their_own_session_and_model(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {}
            for pid, model, sid in ((10, 'gpt-6-astra', '11111111-1111-1111-1111-111111111111'),
                                    (20, 'gpt-5.6-sol', '22222222-2222-2222-2222-222222222222')):
                path = Path(directory) / f'rollout-{sid}.jsonl'
                path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': sid, 'cwd': directory, 'source': 'cli'}}) + '\n' + record(model))
                paths[pid] = str(path)
            with patch.object(detect, '_proc_table', return_value=({10: '/bin/codex', 20: '/bin/codex'}, {})), \
                 patch.object(detect, 'tmux_sessions', return_value=[{'name': 'A', 'created': 1}, {'name': 'B', 'created': 1}]), \
                 patch.object(detect, '_pane_pids', side_effect=lambda name: [10 if name == 'A' else 20]), \
                 patch.object(detect, '_session_cwd', return_value=directory), \
                 patch.object(detect, 'open_files', side_effect=lambda pids: [paths[p] for p in pids]), \
                 patch.object(detect, '_codex_info_for_cwd', return_value=('wrong', 'GPT-5.5')):
                rows = detect.discover_agents()
            self.assertEqual([(r['session_id'], r['label']) for r in rows], [
                ('11111111-1111-1111-1111-111111111111', 'GPT-6 Astra'),
                ('22222222-2222-2222-2222-222222222222', 'GPT-5.6 Sol')])

    def test_subagent_model_cannot_replace_the_parent_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'rollout-root.jsonl'
            child = Path(directory) / 'rollout-child.jsonl'
            root.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'root', 'source': 'cli'}}) + '\n' + record('gpt-6-astra'))
            child.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'child', 'source': {'subagent': {}}}}) + '\n' + record('gpt-5.5'))
            with patch.object(detect, 'open_files', return_value=[str(child), str(root)]):
                self.assertEqual(detect._codex_info_for_processes([10]), ('root', 'GPT-6 Astra'))

    def test_daemon_setup_keeps_the_model_live_instead_of_saving_a_fixed_tag(self):
        with patch.object(detect, 'daemon_model', return_value='GPT-5.5'), \
             patch.object(detect, 'daemon_telegram_bot', return_value=''):
            _, pinned = wizard._daemon_entries({'name': 'Hermes', 'pattern': 'hermes gateway'})
        self.assertNotIn('tag', pinned)


if __name__ == '__main__':
    unittest.main()
