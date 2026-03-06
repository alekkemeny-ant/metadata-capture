'use client';

interface SpreadsheetViewerProps {
  columns: string[];
  rows: (string | number | null)[][];
  totalRows?: number;
  sheetName?: string | null;
  // Edit-mode props — all optional. Absence → current read-only behavior.
  // Wired up in Stage 3; present here so ArtifactModal compiles.
  recordIdColumn?: number;
  enums?: Record<string, string[]>;
  missingRows?: Set<number>;
  onCellCommit?: (recordId: string, column: string, value: string) => Promise<void>;
}

// Plain <table> renderer. No virtualization — fine for ~2000 rows.
// Swap in react-window later if large-file perf becomes a concern.
export default function SpreadsheetViewer({
  columns, rows, totalRows, sheetName,
  // Edit props — unused until Stage 3 lands, but accepted so callers compile.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  recordIdColumn, enums, missingRows, onCellCommit,
}: SpreadsheetViewerProps) {
  const shownRows = rows.length;
  const truncated = typeof totalRows === 'number' && totalRows > shownRows ? totalRows - shownRows : 0;

  if (columns.length === 0 && rows.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-sand-400 text-sm">
        No data
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {sheetName && (
        <div className="px-4 py-1.5 text-xs text-sand-500 border-b border-sand-100 shrink-0">
          Sheet: <span className="font-medium text-sand-600">{sheetName}</span>
        </div>
      )}
      <div className="flex-1 overflow-auto">
        <table className="min-w-full border-collapse text-sm">
          <thead className="sticky top-0 bg-sand-50 z-10">
            <tr>
              <th className="px-3 py-2 text-left text-xs font-medium text-sand-400 border-b border-r border-sand-200 bg-sand-100 w-12">
                #
              </th>
              {columns.map((col, i) => (
                <th
                  key={i}
                  className="px-3 py-2 text-left text-xs font-semibold text-sand-700 border-b border-r border-sand-200 bg-sand-100 whitespace-nowrap"
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri} className={ri % 2 === 0 ? 'bg-white' : 'bg-sand-50/50'}>
                <td className="px-3 py-1.5 text-xs text-sand-400 border-b border-r border-sand-100 tabular-nums text-right">
                  {ri + 1}
                </td>
                {columns.map((_, ci) => {
                  const cell = row[ci];
                  const display = cell == null ? '' : String(cell);
                  const isNumeric = typeof cell === 'number' || /^-?\d+(\.\d+)?$/.test(display);
                  return (
                    <td
                      key={ci}
                      className={`px-3 py-1.5 border-b border-r border-sand-100 max-w-xs truncate ${
                        isNumeric ? 'tabular-nums' : ''
                      }`}
                      title={display}
                    >
                      {display}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {truncated > 0 && (
        <div className="px-4 py-2 text-xs text-sand-500 border-t border-sand-200 bg-sand-50 shrink-0">
          Showing {shownRows} of {totalRows} rows ({truncated} more not shown)
        </div>
      )}
    </div>
  );
}
