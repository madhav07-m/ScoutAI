import { useState, useRef, useEffect } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Plus, History, ChevronDown } from 'lucide-react'
import { useAuth } from '../hooks/useAuth'
import ConfirmDialog from './ConfirmDialog'

export default function Nav({ onNewAnalysis, onHistory }) {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const { user, signOut } = useAuth()
  const [open, setOpen] = useState(false)
  const [confirmingSignOut, setConfirmingSignOut] = useState(false)
  const menuRef = useRef(null)

  const isDashboard = pathname === '/'

  useEffect(() => {
    function handleClick(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  function requestSignOut() {
    setOpen(false)
    setConfirmingSignOut(true)
  }

  async function confirmSignOut() {
    setConfirmingSignOut(false)
    await signOut()
    navigate('/login')
  }

  const initial = user?.email?.[0]?.toUpperCase() || '?'

  return (
    <header className="sticky top-0 z-20 flex items-center justify-between px-5 sm:px-14 py-[22px] border-b border-line bg-paper/90 backdrop-blur-sm">
      <Link to="/" className="flex items-baseline gap-0.5 text-xl font-semibold">
        <span className="serif font-semibold">Scout</span>
        <span className="text-[10px] font-bold tracking-wider bg-ink text-lime px-1.5 py-0.5 rounded -translate-y-1.5 ml-2 font-sans">
          AI
        </span>
      </Link>

      <nav className="hidden sm:flex gap-8 text-sm font-medium">
        <Link
          to="/"
          className={`relative py-1 transition-colors ${
            isDashboard ? 'text-ink after:absolute after:left-0 after:right-0 after:-bottom-[3px] after:h-0.5 after:bg-lime' : 'text-sage hover:text-ink'
          }`}
        >
          Dashboard
        </Link>
        <Link
          to="/companies"
          className={`relative py-1 transition-colors ${
            !isDashboard ? 'text-ink after:absolute after:left-0 after:right-0 after:-bottom-[3px] after:h-0.5 after:bg-lime' : 'text-sage hover:text-ink'
          }`}
        >
          Companies
        </Link>
      </nav>

      <div className="flex items-center gap-2.5">
        {isDashboard && onHistory && (
          <button
            onClick={onHistory}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-full border border-ink text-[13.5px] font-semibold hover:bg-ink hover:text-paper transition-colors"
          >
            <History size={13} strokeWidth={2.4} />
            History
          </button>
        )}
        {isDashboard && onNewAnalysis && (
          <button
            onClick={onNewAnalysis}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-full bg-ink text-lime text-[13.5px] font-semibold hover:bg-[#141B16] transition-colors"
          >
            <Plus size={13} strokeWidth={2.6} />
            New Analysis
          </button>
        )}

        <div className="relative" ref={menuRef}>
          <button
            onClick={() => setOpen((o) => !o)}
            className="flex items-center gap-1.5"
            aria-haspopup="true"
            aria-expanded={open}
          >
            <div className="w-8 h-8 rounded-full bg-ink text-lime flex items-center justify-center font-semibold text-[13px] serif">
              {initial}
            </div>
            <ChevronDown size={12} className="text-sage" />
          </button>

          <AnimatePresence>
            {open && (
              <motion.div
                initial={{ opacity: 0, y: -6, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -6, scale: 0.98 }}
                transition={{ duration: 0.15 }}
                className="absolute right-0 top-[calc(100%+10px)] min-w-[210px] bg-white border border-ink p-1.5 z-50"
              >
                {user ? (
                  <>
                    <div className="px-2.5 py-2 text-xs text-sage break-all border-b border-line mb-1">
                      {user.email}
                    </div>
                    <button
                      onClick={requestSignOut}
                      className="block w-full text-left px-2.5 py-2 text-[13.5px] hover:bg-paper-2"
                    >
                      Sign out
                    </button>
                  </>
                ) : (
                  <Link
                    to="/login"
                    className="block w-full text-left px-2.5 py-2 text-[13.5px] hover:bg-paper-2"
                  >
                    Sign in
                  </Link>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      <ConfirmDialog
        open={confirmingSignOut}
        title="Sign out of Scout AI?"
        message="You'll need to sign in again to start or view analyses."
        confirmLabel="Sign out"
        onConfirm={confirmSignOut}
        onCancel={() => setConfirmingSignOut(false)}
      />
    </header>
  )
}