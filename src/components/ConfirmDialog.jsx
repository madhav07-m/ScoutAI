import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle } from 'lucide-react'

/**
 * Reusable confirm/warning modal, styled to match the app (paper/ink/lime,
 * serif heading, sharp bordered card) instead of the native window.confirm
 * dialog. Controlled — pass `open` and handle the result in `onConfirm` /
 * `onCancel`. Escape and clicking the backdrop both cancel.
 *
 * <ConfirmDialog
 *   open={pending !== null}
 *   title="Sign out of Scout AI?"
 *   message="You'll need to sign in again to continue."
 *   confirmLabel="Sign out"
 *   destructive={false}
 *   onConfirm={...}
 *   onCancel={() => setPending(null)}
 * />
 */
export default function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  destructive = false,
  onConfirm,
  onCancel,
}) {
  const confirmBtnRef = useRef(null)

  useEffect(() => {
    if (!open) return
    confirmBtnRef.current?.focus()
    function handleKey(e) {
      if (e.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [open, onCancel])

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-ink/40 backdrop-blur-[2px] px-5"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) onCancel()
          }}
        >
          <motion.div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="confirm-dialog-title"
            aria-describedby="confirm-dialog-message"
            initial={{ opacity: 0, y: 8, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.97 }}
            transition={{ duration: 0.18 }}
            className="w-full max-w-[380px] bg-paper border border-ink p-6"
          >
            <div className="flex items-start gap-3">
              <div
                className={`flex-shrink-0 w-9 h-9 rounded-full flex items-center justify-center ${
                  destructive ? 'bg-red-bg text-red' : 'bg-lime-dim text-ink'
                }`}
              >
                <AlertTriangle size={17} strokeWidth={2.2} />
              </div>
              <div className="min-w-0">
                <h3 id="confirm-dialog-title" className="serif text-[19px] font-semibold leading-tight">
                  {title}
                </h3>
                {message && (
                  <p id="confirm-dialog-message" className="text-[13.5px] text-sage mt-2 leading-relaxed">
                    {message}
                  </p>
                )}
              </div>
            </div>

            <div className="flex justify-end gap-2.5 mt-6">
              <button
                onClick={onCancel}
                className="px-4 py-2 rounded-full border border-line-2 bg-white text-[13.5px] font-semibold hover:bg-paper-2 transition-colors"
              >
                {cancelLabel}
              </button>
              <button
                ref={confirmBtnRef}
                onClick={onConfirm}
                className={`px-4 py-2 rounded-full text-[13.5px] font-semibold transition-colors ${
                  destructive
                    ? 'bg-red text-white hover:bg-[#8f1c23]'
                    : 'bg-ink text-lime hover:bg-[#141B16]'
                }`}
              >
                {confirmLabel}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body
  )
}