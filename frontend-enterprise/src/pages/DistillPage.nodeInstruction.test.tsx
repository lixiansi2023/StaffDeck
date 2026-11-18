// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';

import { EditableSourceTextLine } from './DistillPage';

afterEach(cleanup);

describe('SOP 节点说明弹窗', () => {
  it('展开后显示完整内容，取消不会修改草稿', async () => {
    const onChange = vi.fn();
    const fullText = `第一段规则说明\n${'核对资金池余额与最低备付额。'.repeat(20)}`;
    const user = userEvent.setup();
    render(<I18nProvider><EditableSourceTextLine label="节点说明" value={fullText} multiline collapsible dialogEditor onChange={onChange} /></I18nProvider>);

    await user.click(screen.getByRole('button', { name: '展开节点说明' }));
    expect((screen.getByRole('textbox', { name: '节点说明完整内容' }) as HTMLTextAreaElement).value).toBe(fullText);
    await user.clear(screen.getByRole('textbox', { name: '节点说明完整内容' }));
    await user.click(screen.getByRole('button', { name: '取消' }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('应用后一次性写回节点草稿', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<I18nProvider><EditableSourceTextLine label="节点说明" value="原说明" multiline dialogEditor onChange={onChange} /></I18nProvider>);

    await user.click(screen.getByRole('button', { name: '展开节点说明' }));
    await user.clear(screen.getByRole('textbox', { name: '节点说明完整内容' }));
    await user.type(screen.getByRole('textbox', { name: '节点说明完整内容' }), '修改后的完整说明');
    await user.click(screen.getByRole('button', { name: '应用到草稿' }));
    expect(onChange).toHaveBeenCalledOnce();
    expect(onChange).toHaveBeenCalledWith('修改后的完整说明');
  });

  it('全屏节点详情中的展开弹窗显示在全屏画布之上', async () => {
    const user = userEvent.setup();
    render(<I18nProvider>
      <div className="z-[80]">
        <EditableSourceTextLine label="节点说明" value="完整节点说明" multiline dialogEditor dialogLayer="fullscreen" onChange={vi.fn()} />
      </div>
    </I18nProvider>);

    await user.click(screen.getByRole('button', { name: '展开节点说明' }));
    expect(screen.getByRole('dialog').className).toContain('z-[100]');
    expect(document.querySelector('[data-slot="dialog-overlay"]')?.className).toContain('z-[90]');
    expect(screen.getByRole('textbox', { name: '节点说明完整内容' })).toHaveProperty('value', '完整节点说明');
  });
});
