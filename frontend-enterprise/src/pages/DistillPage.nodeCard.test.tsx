import { describe, expect, it } from 'vitest';

import {
  buildSkillFlowCanvasLayout,
  previewNodeInstruction,
  SKILL_FLOW_NODE_HEIGHT,
} from './DistillPage';

type LayoutSkill = Parameters<typeof buildSkillFlowCanvasLayout>[0];

function cardForInstruction(instruction: string) {
  const node = { node_id: 'n1', name: '节点一', type: 'collect_info', instruction };
  const skill = {
    skill_id: 'sop_test',
    name: 'SOP',
    description: '',
    required_info: [],
    trigger_intents: [],
    start_node_id: 'n1',
    edges: [],
    nodes: [node],
  } as unknown as LayoutSkill;
  return buildSkillFlowCanvasLayout(skill, [node], {}).nodes[0];
}

describe('SOP 流程图节点卡片', () => {
  it('节点说明最多显示 120 字符，超出时追加……', () => {
    expect(previewNodeInstruction('短说明')).toBe('短说明');
    expect(previewNodeInstruction('中'.repeat(120))).toBe('中'.repeat(120));
    expect(previewNodeInstruction('中'.repeat(121))).toBe(`${'中'.repeat(120)}……`);
    expect(previewNodeInstruction('😀'.repeat(121))).toBe(`${'😀'.repeat(120)}……`);
  });

  it('节点说明为空或含换行时归一显示', () => {
    expect(previewNodeInstruction('')).toBe('暂无说明');
    expect(previewNodeInstruction('  \n ')).toBe('暂无说明');
    expect(previewNodeInstruction('第一行\n第二行  第三行')).toBe('第一行 第二行 第三行');
  });

  it('节点卡片尺寸固定，不随节点说明长度变化', () => {
    const shortCard = cardForInstruction('短说明');
    const longCard = cardForInstruction('中'.repeat(2000));
    expect(shortCard.height).toBe(SKILL_FLOW_NODE_HEIGHT);
    expect(longCard.height).toBe(SKILL_FLOW_NODE_HEIGHT);
    expect(shortCard.width).toBe(longCard.width);
  });
});
