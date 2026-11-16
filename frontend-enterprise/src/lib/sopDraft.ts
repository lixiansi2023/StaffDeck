import type { SkillCard } from '@/types';

function cloneSkill(skill: SkillCard): SkillCard {
  return JSON.parse(JSON.stringify(skill)) as SkillCard;
}

export function uniqueDraftSkillId(skillId: string): string {
  const normalized = (skillId || 'skill')
    .trim()
    .replace(/[^A-Za-z0-9_.-]+/g, '_')
    .replace(/^_+|_+$/g, '')
    || 'skill';
  return `${normalized}_${Date.now().toString(36)}`;
}

export function createBlankSkillCard(): SkillCard {
  const nodeId = 'node_1';
  return {
    skill_id: uniqueDraftSkillId('blank_sop'),
    name: '新建 SOP',
    version: '1.0.0',
    business_domain: '',
    description: '',
    capability_scope: 'general',
    step_timeout_seconds: null,
    trigger_intents: [],
    user_utterance_examples: [],
    goal: [],
    required_info: [],
    response_rules: [],
    nodes: [
      {
        node_id: nodeId,
        type: 'collect_info',
        name: '新节点',
        instruction: '',
        optional: false,
        condition: '',
        expected_user_info: [],
        allowed_actions: [],
        capability_refs: {
          general_skill_ids: [],
          tool_ids: [],
          knowledge_base_ids: [],
          required_general_skill_ids: [],
          required_tool_ids: [],
          required_knowledge_base_ids: [],
        },
        knowledge_scope: {},
        retry_policy: {},
        metadata: {},
      },
    ],
    edges: [],
    start_node_id: nodeId,
    terminal_node_ids: [nodeId],
    interruption_policy: {},
  };
}

export function copySkillCardAsDraft(source: SkillCard): SkillCard {
  const next = cloneSkill(source);
  next.skill_id = uniqueDraftSkillId(source.skill_id || 'sop');
  next.name = (source.name || '新建 SOP') + '（副本）';
  next.version = '1.0.0';
  return next;
}
