import { useState } from 'react';
import type { SpreadsheetPreviewData } from './spreadsheetLoader';
import { MAX_SPREADSHEET_COLS, MAX_SPREADSHEET_ROWS } from './spreadsheetLoader';
import './SpreadsheetArtifactTable.css';

interface SpreadsheetArtifactTableProps {
    data: SpreadsheetPreviewData;
}

export default function SpreadsheetArtifactTable({ data }: SpreadsheetArtifactTableProps) {
    const [activeIndex, setActiveIndex] = useState(0);
    const sheet = data.sheets[activeIndex] ?? data.sheets[0];

    if (!sheet || sheet.rows.length === 0) {
        return (
            <div className="artifact-viewer artifact-viewer--error">Spreadsheet is empty.</div>
        );
    }

    const headerRow = sheet.rows[0] ?? [];
    const bodyRows = sheet.rows.slice(1);

    return (
        <div className="artifact-viewer artifact-viewer--spreadsheet">
            {data.sheets.length > 1 && (
                <div className="spreadsheet-tabs" role="tablist">
                    {data.sheets.map((s, index) => (
                        <button
                            key={s.name}
                            type="button"
                            role="tab"
                            aria-selected={index === activeIndex}
                            className={`spreadsheet-tabs__tab ${index === activeIndex ? 'spreadsheet-tabs__tab--active' : ''}`}
                            onClick={() => setActiveIndex(index)}
                        >
                            {s.name}
                        </button>
                    ))}
                </div>
            )}

            {sheet.truncated && (
                <p className="spreadsheet-notice">
                    Showing up to {MAX_SPREADSHEET_ROWS} rows and {MAX_SPREADSHEET_COLS} columns (
                    {sheet.totalRows}×{sheet.totalCols} total). Save the file to view everything.
                </p>
            )}

            <div className="spreadsheet-table-wrap">
                <table className="spreadsheet-table">
                    <thead>
                        <tr>
                            {headerRow.map((cell, colIndex) => (
                                <th key={colIndex} scope="col">
                                    {cell || `Column ${colIndex + 1}`}
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {bodyRows.map((row, rowIndex) => (
                            <tr key={rowIndex}>
                                {headerRow.map((_, colIndex) => (
                                    <td key={colIndex}>{row[colIndex] ?? ''}</td>
                                ))}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
