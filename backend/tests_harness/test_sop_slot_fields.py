import json
from copy import deepcopy

import pytest
from app.core.slot_hydration_policy import SlotHydrationPolicy
from app.core.task_request_compiler import CapabilityManifest, TaskRequestCompiler
from app.db.models import ChatSession, Skill
from app.session.session_schema import PlannedTaskFrame
from app.session.slot_policy import missing_step_slots
from staffdeck_harness.interactions.sop_adapter import ExecutionSlice
from staffdeck_harness.sop.fields import slot_field_names
from staffdeck_harness.sop.graph import GraphRules
from staffdeck_harness.sop.submission import SopResultValidator

DESCRIPTOR = {'field': 'reimbursement_amount', 'name': '报销金额', 'type': 'number', 'required': True}


@pytest.mark.parametrize('field', [DESCRIPTOR, str(DESCRIPTOR), json.dumps(DESCRIPTOR), 'reimbursement_amount'])
def test_field_descriptors_resolve_to_the_declared_key(field):
    assert slot_field_names([field, 'reimbursement_amount']) == ['reimbursement_amount']


@pytest.mark.parametrize('field', ["{'field': ''}", '{"name":"amount"}', '{not a descriptor}', "{'field': __import__('os').system('false')}"])
def test_invalid_descriptors_remain_required_and_are_never_executed(field):
    assert slot_field_names([field]) == [field]


def test_field_descriptors_never_execute_python_expressions(tmp_path):
    marker = tmp_path / 'must-not-exist'
    text = "{'field': __import__('pathlib').Path(" + repr(str(marker)) + ").write_text('unexpected')}"
    assert slot_field_names([text]) == [text]
    assert not marker.exists()


@pytest.mark.parametrize('amount', [80, 100, 120])
def test_amount_fields_compile_and_complete_without_mutating_the_definition(amount):
    raw = str(DESCRIPTOR)
    content = {'skill_id': 'expense', 'name': 'Expense', 'start_node_id': 'collect',
        'required_info': [raw], 'terminal_node_ids': ['reply'],
        'nodes': [{'node_id': 'collect', 'name': 'Amount', 'expected_user_info': [raw]},
                  {'node_id': 'reply', 'name': 'Reply', 'allowed_actions': ['answer_user']}],
        'edges': [{'source_node_id': 'collect', 'next_node_id': 'reply'}]}
    original = deepcopy(content)
    skill = Skill(id='expense', tenant_id='tenant', skill_id='expense', name='Expense', content_json=content)
    session = ChatSession(id='session', tenant_id='tenant', active_skill_id='expense', active_step_id='collect',
        slots_json={'reimbursement_amount': amount})
    frame = PlannedTaskFrame(task_id='frame', kind='sop', target_skill_id='expense', target_step_id='collect')
    requirement = TaskRequestCompiler().compile(frame, session, skill, CapabilityManifest())
    assert requirement.expected_slots == ['reimbursement_amount']
    assert requirement.required_slots == []
    assert requirement.known_slots == {'reimbursement_amount': amount}
    assert SlotHydrationPolicy.skill_expected_fields(skill) == {'reimbursement_amount'}
    assert GraphRules.terminal_position(content, 'reply', session.slots_json)
    assert content == original
    session.slots_json = {}
    missing = TaskRequestCompiler().compile(frame, session, skill, CapabilityManifest())
    assert missing.required_slots == ['reimbursement_amount']
    assert not GraphRules.terminal_position(content, 'reply', {})


def test_result_fields_are_separate_from_user_input_and_required_on_submission():
    content = {
        'skill_id': 'review', 'name': 'Review', 'start_node_id': 'aggregate',
        'required_info': [], 'terminal_node_ids': ['aggregate'],
        'nodes': [{'node_id': 'aggregate', 'name': '汇总', 'instruction': '汇总审阅结论',
                   'expected_user_info': [], 'result_inputs': ['review_08'],
                   'result_outputs': ['audit_payload']}],
        'edges': [],
    }
    skill = Skill(id='review', tenant_id='tenant', skill_id='review', name='Review',
                  content_json=content)
    session = ChatSession(id='session', tenant_id='tenant', active_skill_id='review',
                          active_step_id='aggregate', slots_json={'review_08': {'level': '通过'},
                                                                   'review_01': {'level': '风险'},
                                                                   'audit_payload': {'old': True}})
    frame = PlannedTaskFrame(task_id='frame', kind='sop', target_skill_id='review',
                             target_step_id='aggregate')
    requirement = TaskRequestCompiler().compile(frame, session, skill, CapabilityManifest())
    assert requirement.expected_slots == []
    assert requirement.required_slots == []
    assert requirement.result_outputs == ['audit_payload']
    assert requirement.known_slots == {'review_08': {'level': '通过'}}
    assert GraphRules.current_step(content, 'aggregate')['result_inputs'] == ['review_08']
    assert GraphRules.current_step(content, 'aggregate')['result_outputs'] == ['audit_payload']
    assert '读取已有步骤结果：review_08' in requirement.requirements
    assert missing_step_slots(requirement, {}) == ['audit_payload']
    assert missing_step_slots(requirement, {'audit_payload': {'items': []}}) == []
    rejected = SopResultValidator(requirement).submit(
        {'status': 'completed', 'reply_fragment': '已汇总', 'slot_updates': {}},
        allowed_next_steps=set(), capability_results=[], citations=[], evidence=[],
    )
    assert not rejected.success


def test_result_field_configuration_is_injected_at_runtime_not_node_instruction():
    slice_ = ExecutionSlice(
        skill_id='review', skill_name='Review', version='1.0.0', node_id='aggregate',
        node={'name': '汇总', 'instruction': '汇总审阅结论',
              'result_inputs': ['review_08'], 'result_outputs': ['audit_payload']},
        transitions=(), slots={'review_08': {'level': '通过'},
                               'review_01': {'level': '风险'}},
        expected_user_info=(), allowed_actions=(), resolved_slots=(),
        is_terminal=True, declares_handoff=False, sub_sop_id=None, goal='汇总',
    )
    prompt = slice_.as_prompt()
    assert '汇总审阅结论' in prompt
    assert '读取已有步骤结果：review_08' in prompt
    assert '本步骤结果字段（提交时写入 slot_updates）：audit_payload' in prompt
    assert 'review_01=' not in prompt
