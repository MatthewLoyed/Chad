export type ColumnId = 'todo' | 'in_progress' | 'needs_review' | 'done';

export interface KanbanCard {
    id: string;
    title: string;
    description: string | null;
    column_id: ColumnId;
    order_index: number;
}
