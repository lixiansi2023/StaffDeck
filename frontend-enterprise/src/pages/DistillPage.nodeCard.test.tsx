// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  buildSkillFlowCanvasLayout,
  buildActionOptions,
  EditableSourceActionLine,
  filterActionOptionsForNodeType,
  previewNodeInstruction,
  SKILL_FLOW_NODE_HEIGHT,
  SkillFlowNodeCard,
  skillGraphSteps,
} from './DistillPage';
import { withoutSkillNodeFlowPositions } from './skillFlowModel';

type LayoutSkill = Parameters<typeof buildSkillFlowCanvasLayout>[0];

afterEach(cleanup);

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
  it('点击展开只打开节点详情，不触发节点选择', async () => {
    const onOpenDetails = vi.fn();
    const onSelect = vi.fn();
    const onToggle = vi.fn();
    render(<SkillFlowNodeCard
      index={0}
      step={{ node_id: 'n1', name: '审核表单', type: 'collect_info', instruction: '核对附件正文' }}
      terminal={false}
      selectedPaths={[]}
      highlightedPaths={[]}
      updatingPaths={[]}
      dirtyPaths={[]}
      textDiffs={[]}
      selected={false}
      connecting={false}
      dropTarget={false}
      connectHandleScale={1}
      showConnectHandle={false}
      onConnectClick={vi.fn()}
      onConnectStart={vi.fn()}
      onSelect={onSelect}
      onOpenDetails={onOpenDetails}
      onToggle={onToggle}
    />);

    await userEvent.click(screen.getByRole('button', { name: '展开节点 1 详情' }));
    expect(onOpenDetails).toHaveBeenCalledOnce();
    expect(onSelect).not.toHaveBeenCalled();
    expect(onToggle).not.toHaveBeenCalled();
    cleanup();
  });

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

  it('步骤结果配置与期望字段分别保留', () => {
    const skill = {
      skill_id: 'sop_test', name: 'SOP', description: '', required_info: [],
      trigger_intents: [], start_node_id: 'n1', edges: [],
      nodes: [{ node_id: 'n1', name: '汇总', type: 'collect_info',
        instruction: '汇总审阅结论', expected_user_info: [],
        result_inputs: ['review_08'], result_outputs: ['audit_payload'] }],
    } as unknown as LayoutSkill;
    const [step] = skillGraphSteps(skill);
    expect(step.expected_user_info).toEqual([]);
    expect(step.result_inputs).toEqual(['review_08']);
    expect(step.result_outputs).toEqual(['audit_payload']);
    expect(step.instruction).toBe('汇总审阅结论');
  });

  it('自动排列恢复按流程层级计算的节点位置', () => {
    const skill = {
      skill_id: 'sop_test', name: 'SOP', description: '', required_info: [],
      trigger_intents: [], start_node_id: 'n1',
      edges: [{ source_node_id: 'n1', next_node_id: 'n2', priority: 1 }],
      nodes: [
        { node_id: 'n1', name: '收集材料', metadata: { flow_position: { x: 900, y: 900 } } },
        { node_id: 'n2', name: '审核材料', metadata: { flow_position: { x: 100, y: 120 } } },
      ],
    } as unknown as LayoutSkill;
    const arranged = withoutSkillNodeFlowPositions(skill);
    const layout = buildSkillFlowCanvasLayout(arranged, skillGraphSteps(arranged));

    expect(layout.nodes[0].x).toBe(layout.nodes[1].x);
    expect(layout.nodes[0].y).toBeLessThan(layout.nodes[1].y);
    expect(arranged.edges).toEqual(skill.edges);
  });

  it('手工节点可选择内置文件能力且无需业务工具目录', async () => {
    const onChange = vi.fn();
    const options = buildActionOptions({}, {}, []);
    expect(options.find((option) => option.value === 'call_tool:read_file')?.group).toBe('内置文件能力');
    expect(options.find((option) => option.value === 'call_tool:extract_document_text')?.group).toBe('内置文件能力');
    expect(options.some((option) => option.value === 'call_tool:exec_command')).toBe(false);

    render(<EditableSourceActionLine
      values={[]}
      options={options}
      toolDescriptions={{}}
      toolStatuses={{}}
      onChange={onChange}
    />);
    await userEvent.click(screen.getByRole('button', { name: '添加动作' }));
    await userEvent.click(screen.getByRole('button', { name: /提取文档内容/ }));
    expect(onChange).toHaveBeenCalledWith('call_tool:extract_document_text');
  });

  it('内置动作回显与业务工具保持分组', () => {
    const options = buildActionOptions({ 'audit.save': '保存审核结果' }, {}, [
      { allowed_actions: ['call_tool:read_file'] },
    ]);
    expect(options.find((option) => option.value === 'call_tool:audit.save')?.group).toBe('业务工具');
    render(<EditableSourceActionLine
      values={['call_tool:read_file']}
      options={options}
      toolDescriptions={{}}
      toolStatuses={{}}
      onChange={vi.fn()}
    />);
    expect(screen.getByText('内置能力：读取文本文件')).toBeTruthy();
  });

  it('发布可下载文件只供最终回复节点选择', async () => {
    const options = buildActionOptions({}, {}, []);
    const publish = 'call_tool:publish_artifact';
    expect(options.find((option) => option.value === publish)?.group).toBe('内置交付能力');
    expect(filterActionOptionsForNodeType(options, 'tool_call').some((option) => option.value === publish)).toBe(false);
    expect(filterActionOptionsForNodeType(options, 'response', false).some((option) => option.value === publish)).toBe(false);
    const finalOptions = filterActionOptionsForNodeType(options, 'response', true);
    expect(finalOptions.some((option) => option.value === publish)).toBe(true);

    const onChange = vi.fn();
    render(<EditableSourceActionLine
      values={[]}
      options={finalOptions}
      toolDescriptions={{}}
      toolStatuses={{}}
      onChange={onChange}
    />);
    await userEvent.click(screen.getByRole('button', { name: '添加动作' }));
    await userEvent.click(screen.getByRole('button', { name: /发布可下载文件/ }));
    expect(onChange).toHaveBeenCalledWith(publish);
  });
});
