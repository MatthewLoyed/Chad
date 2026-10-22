import type * as XLSX from 'xlsx';

export const MAX_SPREADSHEET_ROWS = 500;
export const MAX_SPREADSHEET_COLS = 40;

export interface SpreadsheetSheet {
    name: string;
    rows: string[][];
    truncated: boolean;
    totalRows: number;
    totalCols: number;
}

export interface SpreadsheetPreviewData {
    sheets: SpreadsheetSheet[];
}

function cellToString(value: unknown): string {
    if (value === null || value === undefined) return '';
    if (value instanceof Date) return value.toISOString();
    return String(value);
}

function sheetToRows(
    XLSX: typeof import('xlsx'),
    sheet: XLSX.WorkSheet,
): { rows: string[][]; totalRows: number; totalCols: number } {
    const ref = sheet['!ref'];
    if (!ref) {
        return { rows: [], totalRows: 0, totalCols: 0 };
    }

    const range = XLSX.utils.decode_range(ref);
    const totalRows = range.e.r - range.s.r + 1;
    const totalCols = range.e.c - range.s.c + 1;
    const rowLimit = Math.min(totalRows, MAX_SPREADSHEET_ROWS);
    const colLimit = Math.min(totalCols, MAX_SPREADSHEET_COLS);

    const rows: string[][] = [];
    for (let r = range.s.r; r < range.s.r + rowLimit; r++) {
        const row: string[] = [];
        for (let c = range.s.c; c < range.s.c + colLimit; c++) {
            const addr = XLSX.utils.encode_cell({ r, c });
            row.push(cellToString(sheet[addr]?.v));
        }
        rows.push(row);
    }

    return { rows, totalRows, totalCols };
}

export async function loadSpreadsheetPreview(
    fileUrl: string,
    extension: string
): Promise<SpreadsheetPreviewData> {
    const response = await fetch(fileUrl);
    if (!response.ok) {
        throw new Error(`Failed to load file (${response.status})`);
    }

    // Lazy-load the heavy xlsx parser only when a spreadsheet is actually viewed
    const XLSX = await import('xlsx');

    const buffer = await response.arrayBuffer();
    const workbook =
        extension === 'csv'
            ? XLSX.read(new TextDecoder().decode(buffer), { type: 'string' })
            : XLSX.read(buffer, { type: 'array' });

    const sheets: SpreadsheetSheet[] = workbook.SheetNames.map(name => {
        const { rows, totalRows, totalCols } = sheetToRows(XLSX, workbook.Sheets[name]);
        return {
            name,
            rows,
            truncated: totalRows > MAX_SPREADSHEET_ROWS || totalCols > MAX_SPREADSHEET_COLS,
            totalRows,
            totalCols,
        };
    });

    if (sheets.length === 0) {
        throw new Error('No sheets found in spreadsheet.');
    }

    return { sheets };
}
