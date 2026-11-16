import { describe, expect, it } from 'vitest';

import type { SkillCard } from '@/types';

import { copySkillCardAsDraft, createBlankSkillCard } from '@/lib/sopDraft';

function sampleSkill(): SkillCard {
  return {
    skill_id: 'source_sop',
    name: '建设工程招标文件呈批表',
    version: '2.3.0',
    description: '源 SOP',
    trigger_intents: ['招标'],
    user_utterance_examples: [],
    goal: [],
    required_info: [],
    response_rules: [],
    nodes: [{ node_id: 'n1', type: 'collect_info', name: '节点一', instruction: '说明' }],
    edges: [{ source_node_id: 'n1', next_node_id: 'n1' }],
    start_node_id: 'n1',
    terminal_node_ids: ['n1'],
    interruption_policy: {},
  };
}

describe('SOP 新建空白与复制', () => {
  it('空白 SOP 带一个可编辑节点且起止节点一致', () => {
    const blank = createBlankSkillCard();
    expect(blank.skill_id).toBeTruthy();
    expect(blank.name).toBeTruthy();
    expect(blank.version).toBe('1.0.0');
    expect(blank.nodes).toHaveLength(1);
    expect(blank.start_node_id).toBe(blank.nodes[0].node_id);
    expect(blank.terminal_node_ids).toContain(blank.nodes[0].node_id);
    expect(blank.nodes[0].instruction).toBe('');
  });

  it('复制 SOP 生成新 ID、副本名称并保留结构', () => {
    const source = sampleSkill();
    const copy = copySkillCardAsDraft(source);
    expect(copy.skill_id).not.toBe(source.skill_id);
    expect(copy.name).toContain('（副本）');
    expect(copy.version).toBe('1.0.0');
    expect(copy.nodes).toEqual(source.nodes);
    expect(copy.edges).toEqual(source.edges);
    expect(copy.start_node_id).toBe(source.start_node_id);
    expect(source.name).toBe('建设工程招标文件呈批表');
  });

  it('修改复制草稿不会影响原 SOP', () => {
    const source = sampleSkill();
    const copy = copySkillCardAsDraft(source);
    copy.nodes[0].name = '改名';
    expect(source.nodes[0].name).toBe('节点一');
  });
});
