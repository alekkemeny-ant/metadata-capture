/**
 * EditableCell state machine.
 *
 * Four states: isEditing / draft / isSaving / error. The machine's job is
 * to make the commit semantics unambiguous — especially the two subtle ones:
 *
 *   1. No-change commit skips the network entirely (not "returns early
 *      after a 200", literally never calls onCommit).
 *   2. Save failure stays in edit mode with the error shown, so the user
 *      can fix their input and retry. Reverting silently would lose work.
 *
 * Rendering note: EditableCell clones a `defaultTd` (the upstream claude.ai
 * <td>) to preserve its styling. The tests supply a minimal stub <td>.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { EditableCell } from '../SpreadsheetViewer';

// EditableCell clones a <td>, which needs a table ancestor for jsdom's
// parser to keep it. Wrap in a minimal table.
const wrap = (cell: React.ReactElement) => (
  <table><tbody><tr>{cell}</tr></tbody></table>
);

// In production defaultTd is the upstream TableView's fully-styled <td>.
// For tests we only need its shape (a <td> element to clone).
const stubTd = <td className="border border-gray-300 px-2 py-1 text-sm cursor-cell" />;

const baseProps = {
  defaultTd: stubTd,
  value: 'wt' as string | null,
  mode: 'text' as const,
  isSelected: false,
  onSelect: () => {},
  onCommit: async () => {},
};

describe('EditableCell — display mode', () => {
  it('renders the value', () => {
    render(wrap(<EditableCell {...baseProps} />));
    expect(screen.getByText('wt')).toBeInTheDocument();
  });

  it('first click calls onSelect, not onCommit', async () => {
    const onSelect = vi.fn();
    const onCommit = vi.fn();
    render(wrap(<EditableCell {...baseProps} onSelect={onSelect} onCommit={onCommit} />));

    await userEvent.click(screen.getByText('wt'));

    expect(onSelect).toHaveBeenCalledOnce();
    expect(onCommit).not.toHaveBeenCalled();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('readonly mode never enters edit on second click', async () => {
    const onCommit = vi.fn();
    render(wrap(
      <EditableCell {...baseProps} mode="readonly" isSelected onCommit={onCommit} />
    ));

    await userEvent.click(screen.getByText('wt'));

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(onCommit).not.toHaveBeenCalled();
  });
});

describe('EditableCell — text mode edit transitions', () => {
  it('second click on a selected cell enters edit mode', async () => {
    render(wrap(<EditableCell {...baseProps} isSelected />));

    await userEvent.click(screen.getByText('wt'));

    const input = screen.getByRole('textbox');
    expect(input).toHaveValue('wt'); // draft initialized from value
    expect(input).toHaveFocus(); // autoFocus
  });

  it('Enter key on a selected cell enters edit mode', async () => {
    render(wrap(<EditableCell {...baseProps} isSelected />));

    screen.getByText('wt').focus();
    await userEvent.keyboard('{Enter}');

    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  it('typing a printable char on a selected cell enters edit with that char as draft', async () => {
    // Spreadsheet convention: type-to-replace.
    render(wrap(<EditableCell {...baseProps} isSelected />));

    screen.getByText('wt').focus();
    await userEvent.keyboard('X');

    expect(screen.getByRole('textbox')).toHaveValue('X');
  });

  it('Escape reverts draft and exits edit without calling onCommit', async () => {
    const onCommit = vi.fn();
    render(wrap(<EditableCell {...baseProps} isSelected onCommit={onCommit} />));

    await userEvent.click(screen.getByText('wt'));
    const input = screen.getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.type(input, 'changed');
    await userEvent.keyboard('{Escape}');

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.getByText('wt')).toBeInTheDocument(); // reverted
    expect(onCommit).not.toHaveBeenCalled();
  });
});

describe('EditableCell — commit semantics', () => {
  it('Enter commits the draft', async () => {
    const onCommit = vi.fn().mockResolvedValue(undefined);
    render(wrap(<EditableCell {...baseProps} isSelected onCommit={onCommit} />));

    await userEvent.click(screen.getByText('wt'));
    const input = screen.getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.type(input, 'Pvalb-IRES-Cre');
    await userEvent.keyboard('{Enter}');

    expect(onCommit).toHaveBeenCalledWith('Pvalb-IRES-Cre');
  });

  it('no-change commit skips onCommit entirely — the network short-circuit', async () => {
    const onCommit = vi.fn();
    render(wrap(<EditableCell {...baseProps} isSelected onCommit={onCommit} />));

    await userEvent.click(screen.getByText('wt'));
    await userEvent.keyboard('{Enter}'); // draft still === 'wt'

    expect(onCommit).not.toHaveBeenCalled();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument(); // but did exit edit
  });

  it('whitespace-only change is treated as no-change (trimmed)', async () => {
    const onCommit = vi.fn();
    render(wrap(<EditableCell {...baseProps} isSelected onCommit={onCommit} />));

    await userEvent.click(screen.getByText('wt'));
    const input = screen.getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.type(input, '  wt  ');
    await userEvent.keyboard('{Enter}');

    expect(onCommit).not.toHaveBeenCalled();
  });

  it('onCommit rejection stays in edit mode with error visible', async () => {
    // The PATCH endpoint rejects unknown fields with 400 + a detail message.
    // That message must reach the user, and their draft must not be lost.
    const onCommit = vi.fn().mockRejectedValue(
      new Error("Unknown field 'nonsense' for record type 'subject'"),
    );
    render(wrap(<EditableCell {...baseProps} isSelected onCommit={onCommit} />));

    await userEvent.click(screen.getByText('wt'));
    const input = screen.getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.type(input, 'newval');
    await userEvent.keyboard('{Enter}');

    // Still editing:
    expect(await screen.findByRole('textbox')).toHaveValue('newval');
    // Error surfaced:
    expect(screen.getByText(/Unknown field 'nonsense'/)).toBeInTheDocument();
  });

  it('after a failed commit, Escape still reverts cleanly', async () => {
    const onCommit = vi.fn().mockRejectedValue(new Error('nope'));
    render(wrap(<EditableCell {...baseProps} isSelected onCommit={onCommit} />));

    await userEvent.click(screen.getByText('wt'));
    const input = screen.getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.type(input, 'bad');
    await userEvent.keyboard('{Enter}');
    await screen.findByText('nope');

    await userEvent.keyboard('{Escape}');

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByText('nope')).not.toBeInTheDocument();
    expect(screen.getByText('wt')).toBeInTheDocument();
  });
});

describe('EditableCell — enum mode', () => {
  const enumProps = {
    ...baseProps,
    mode: 'enum' as const,
    value: 'Male',
    enumValues: ['Female', 'Male'],
    isSelected: true,
  };

  it('shows the enum dropdown hint when selected in display mode', () => {
    render(wrap(<EditableCell {...enumProps} />));
    expect(screen.getByTestId('enum-hint')).toBeInTheDocument();
  });

  it('second click opens a <select> with all enum values', async () => {
    render(wrap(<EditableCell {...enumProps} />));

    await userEvent.click(screen.getByText('Male'));

    const select = screen.getByRole('combobox');
    const opts = Array.from(select.querySelectorAll('option')).map((o) => o.textContent);
    expect(opts).toEqual(['Female', 'Male']);
  });

  it('includes a "(current)" option when value is not in the enum list', async () => {
    // Covers out-of-vocab data — the record might have a species the schema
    // doesn't recognize. Don't lose the current value.
    render(wrap(
      <EditableCell {...enumProps} value="Unknown" enumValues={['Female', 'Male']} />
    ));

    await userEvent.click(screen.getByText('Unknown'));

    expect(screen.getByRole('option', { name: 'Unknown (current)' })).toBeInTheDocument();
  });

  it('selecting an option commits immediately', async () => {
    const onCommit = vi.fn().mockResolvedValue(undefined);
    render(wrap(<EditableCell {...enumProps} onCommit={onCommit} />));

    await userEvent.click(screen.getByText('Male'));
    await userEvent.selectOptions(screen.getByRole('combobox'), 'Female');

    expect(onCommit).toHaveBeenCalledWith('Female');
  });
});

describe('EditableCell — external sync', () => {
  it('draft follows value prop when not editing', () => {
    const { rerender } = render(wrap(<EditableCell {...baseProps} value="old" />));
    expect(screen.getByText('old')).toBeInTheDocument();

    // ArtifactModal updates the overlay map after a PATCH elsewhere →
    // new rows → new value prop. Display should track it.
    rerender(wrap(<EditableCell {...baseProps} value="new" />));
    expect(screen.getByText('new')).toBeInTheDocument();
  });

  it('draft does NOT follow value prop while editing — edits are not clobbered', async () => {
    const { rerender } = render(wrap(<EditableCell {...baseProps} value="old" isSelected />));

    await userEvent.click(screen.getByText('old'));
    const input = screen.getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.type(input, 'my-in-progress-edit');

    // Concurrent overlay update arrives:
    rerender(wrap(<EditableCell {...baseProps} value="concurrent-change" isSelected />));

    // User's draft wins.
    expect(screen.getByRole('textbox')).toHaveValue('my-in-progress-edit');
  });
});
