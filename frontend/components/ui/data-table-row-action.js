import Button from './button';

export default function DataTableRowAction({ children, variant = 'ghost', ...props }) {
  return (
    <Button
      variant={variant}
      size="sm"
      className="min-w-[64px] justify-center"
      {...props}
      onClick={(event) => {
        event.stopPropagation();
        props.onClick?.(event);
      }}
    >
      {children}
    </Button>
  );
}
