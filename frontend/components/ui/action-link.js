import Button from './button';

/**
 * Navigation that should read as an action while retaining link semantics.
 * Use this instead of hand-built anchor/button classes for user-facing CTAs.
 */
export default function ActionLink({ children, variant = 'tertiary', size = 'sm', ...props }) {
  return <Button variant={variant} size={size} {...props}>{children}</Button>;
}
