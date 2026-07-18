import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef
} from "@tanstack/react-table";

export type DataTableColumn<T> = ColumnDef<T>;

export function DataTable<T>({
  data,
  columns,
  empty,
  getRowKey
}: {
  data: T[];
  columns: DataTableColumn<T>[];
  empty: string;
  getRowKey?: (row: T, index: number) => string;
}) {
  const table = useReactTable({ data, columns, getCoreRowModel: getCoreRowModel() });
  const visibleColumns = table.getAllLeafColumns().length;

  return (
    <div className="ui-table-wrap">
      <table>
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => (
                <th key={header.id}>
                  {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={getRowKey ? getRowKey(row.original, row.index) : row.id}>
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
              ))}
            </tr>
          ))}
          {!data.length ? (
            <tr>
              <td colSpan={visibleColumns}>{empty}</td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}
