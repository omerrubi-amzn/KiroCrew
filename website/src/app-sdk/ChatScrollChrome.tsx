/**
 * ChatScrollChrome — shared visual chrome for a chat transcript scroller:
 * the top/bottom edge fades and the jump-to-bottom pill. Extracted from the
 * main chat page so every chat surface (ChatPane split panes, the Crew
 * Members thread, embeds) wears the same edges instead of hand-rolling them.
 *
 * Layout contract (matches how the main chat mounts its own copies):
 *   - <EdgeFade side="top">  goes in a zero-height `relative` wrapper placed
 *     BETWEEN the header and the scroller; it overlays the scroller's first
 *     24px so content dissolves under the header edge instead of clipping.
 *   - <EdgeFade side="bottom"> goes directly AFTER the scroller; its in-flow
 *     height is cancelled with a negative top margin so it overlays the
 *     scroller's last 24px above the composer.
 *   - <JumpToBottomButton> goes inside a `relative` wrapper around the
 *     composer block; it floats 40px above it, centred, and is pointer-inert
 *     except for the pill itself.
 *
 * Deliberately i18n-free: the pill label is a required prop so this component
 * stays catalog-independent (hosts pass their own translated string).
 */
import { ArrowDown } from 'lucide-react'

export function EdgeFade({ side }: { side: 'top' | 'bottom' }) {
  if (side === 'top') {
    return (
      <div aria-hidden className="absolute top-0 inset-x-0 h-6 bg-gradient-to-b from-bg to-transparent pointer-events-none" />
    )
  }
  return (
    <div aria-hidden className="h-6 -mt-6 bg-gradient-to-t from-bg to-transparent pointer-events-none relative z-[1]" />
  )
}

export function JumpToBottomButton({ visible, onClick, label }: {
  visible: boolean
  onClick: () => void
  /** Translated aria-label, supplied by the host (keeps this catalog-free). */
  label: string
}) {
  if (!visible) return null
  return (
    <div className="absolute -top-10 inset-x-0 z-10 pointer-events-none flex justify-center">
      <button
        className="w-8 h-8 rounded-full flex items-center justify-center cursor-pointer pointer-events-auto transition-all duration-200 bg-bg-elevated border border-border-strong text-text hover:bg-bg-hover hover:border-accent hover:scale-[1.06] active:scale-95 active:duration-75 shadow-md"
        onClick={onClick}
        aria-label={label}
      ><ArrowDown size={14} strokeWidth={2.5} /></button>
    </div>
  )
}
