'use client';

import { useState, useEffect } from 'react';
import { fetchModels } from '../lib/api';

interface ModelPickerProps {
  value: string;
  onChange: (model: string) => void;
  disabled?: boolean;
}

/**
 * Model selector — moved from ChatPanel's bottom-left corner to the top of
 * the chat page. The chat page owns the state (it needs to pass the model to
 * sendChatMessage), this component just fetches the option list and renders
 * the dropdown.
 */
export default function ModelPicker({ value, onChange, disabled }: ModelPickerProps) {
  const [models, setModels] = useState<string[]>([]);

  useEffect(() => {
    fetchModels().then((info) => {
      setModels(info.models);
      // Initialize parent state if it's empty
      if (!value && info.default) onChange(info.default);
    });
    // onChange/value intentionally omitted — run once on mount. The parent's
    // default will be set by this effect, after which the parent drives value.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (models.length === 0) return null;

  return (
    <div className="relative inline-block">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="appearance-none text-xs text-sand-500 bg-sand-50 border border-sand-200 rounded-lg
                   hover:text-sand-700 focus:text-sand-700 focus:outline-none focus:ring-2 focus:ring-brand-fig/30
                   cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed
                   pl-2.5 pr-6 py-1.5"
        title="Select model"
      >
        {models.map((m) => (
          <option key={m} value={m}>
            {m.replace('claude-', '').replace(/-\d{8}$/, '')}
          </option>
        ))}
      </select>
      <svg
        className="pointer-events-none absolute right-1.5 top-1/2 -translate-y-1/2 w-3 h-3 text-sand-400"
        fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
      </svg>
    </div>
  );
}
