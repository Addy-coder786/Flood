/**
 * Utility function to combine class names, standard in shadcn/ui.
 * Filters out falsy values and joins with a space.
 */
export function cn(...inputs) {
  return inputs.filter(Boolean).join(' ')
}
