import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Trash2 } from 'lucide-react'
import { supabase } from '../lib/supabase'
import { useAuth } from '../hooks/useAuth'
import ConfirmDialog from './ConfirmDialog'

export default function HistoryPanel({ onClose, onOpenRow }) {
  const { user } = useAuth()
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)
  // { type: 'one', id } | { type: 'all' } | null
  const [pendingDelete, setPendingDelete] = useState(null)

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function load() {
    if (!user) {
      setRows([])
      return
    }
    setError(null)
    const { data, error } = await supabase
      .from('analyses')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(50)
    if (error) {
      setError(error.message)
      setRows([])
      return
    }
    setRows(data)
  }

  function requestDeleteOne(id) {
    setPendingDelete({ type: 'one', id })
  }

  function requestClearAll() {
    setPendingDelete({ type: 'all' })
  }

  async function confirmPendingDelete() {
    const pending = pendingDelete
    setPendingDelete(null)
    if (!pending) return
    if (pending.type === 'one') {
      const { error } = await supabase.from('analyses').delete().eq('id', pending.id)
      if (error) return alert("Couldn't delete: " + error.message)
    } else {
      const { error } = await supabase.from('analyses').delete().eq('user_id', user.id)
      if (error) return alert("Couldn't clear history: " + error.message)
    }
    load()
  }

  return (
    <section className="relative z-[2] max-w-[1120px] mx-auto px-5 sm:px-14 pb-24">
      <div className="flex items-baseline justify-between mb-6.5">
        <h2 className="serif text-[30px] font-semibold">Your past analyses</h2>
        <div className="flex items-center gap-2.5">
          {rows?.length > 0 && (
            <button
              onClick={requestClearAll}
              className="border border-line-2 bg-white text-red text-[12.5px] font-semibold px-4 py-2 rounded-full hover:border-red hover:bg-red-bg transition-colors"
            >
              Clear all
            </button>
          )}
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-full border border-ink text-[13.5px] font-semibold hover:bg-ink hover:text-paper transition-colors"
          >
            ✕ Close
          </button>
        </div>
      </div>

      {!user && <p className="text-sage">Sign in to see your analysis history.</p>}
      {user && error && <p className="text-red">Couldn't load history: {error}</p>}
      {user && rows === null && !error && <p className="text-sage">Loading…</p>}
      {user && rows?.length === 0 && !error && (
        <p className="text-sage">No analyses yet — run one from "New Analysis".</p>
      )}

      <AnimatePresence>
        {rows?.map((row) => (
          <motion.div
            key={row.id}
            layout
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0, height: 0, marginBottom: 0 }}
            transition={{ duration: 0.2 }}
            onClick={() => onOpenRow(row)}
            className="flex items-center justify-between gap-3.5 bg-white border border-line border-l-2 border-l-ink px-5 py-4 mb-2 cursor-pointer hover:border-l-lime hover:bg-paper-2 transition-colors"
          >
            <div>
              <div className="font-semibold text-sm">{row.jd_name || 'Untitled JD'}</div>
              <div className="text-xs text-sage mt-0.5">
                {new Date(row.created_at).toLocaleString()} · {row.resume_count || 0} resume
                {row.resume_count === 1 ? '' : 's'}
              </div>
            </div>
            <div className="flex items-center gap-3.5 flex-shrink-0">
              <div className="flex gap-2">
                <span className="text-[11.5px] font-bold px-2.5 py-0.5 rounded-full bg-green-ok-bg text-green-ok">
                  {row.counts?.Strong ?? 0}
                </span>
                <span className="text-[11.5px] font-bold px-2.5 py-0.5 rounded-full bg-amber-bg text-amber">
                  {row.counts?.Average ?? 0}
                </span>
                <span className="text-[11.5px] font-bold px-2.5 py-0.5 rounded-full bg-red-bg text-red">
                  {row.counts?.Weak ?? 0}
                </span>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  requestDeleteOne(row.id)
                }}
                title="Delete this analysis"
                aria-label="Delete this analysis"
                className="flex items-center justify-center w-[30px] h-[30px] border border-line-2 bg-white text-sage hover:border-red hover:text-red hover:bg-red-bg transition-colors"
              >
                <Trash2 size={15} />
              </button>
            </div>
          </motion.div>
        ))}
      </AnimatePresence>

      <ConfirmDialog
        open={pendingDelete !== null}
        title={pendingDelete?.type === 'all' ? 'Clear your entire history?' : 'Delete this analysis?'}
        message={
          pendingDelete?.type === 'all'
            ? "Every saved analysis will be permanently removed. This can't be undone."
            : "This analysis will be permanently removed from your history. This can't be undone."
        }
        confirmLabel={pendingDelete?.type === 'all' ? 'Clear all' : 'Delete'}
        destructive
        onConfirm={confirmPendingDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </section>
  )
}