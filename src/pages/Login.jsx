import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { supabase } from '../lib/supabase'
import { useAuth } from '../hooks/useAuth'

export default function Login() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [mode, setMode] = useState('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (user) navigate('/', { replace: true })
  }, [user, navigate])

  function toggleMode() {
    setMode((m) => (m === 'signin' ? 'signup' : 'signin'))
    setError('')
    setSuccess('')
  }

  async function handleGoogle() {
    await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: window.location.origin + '/' },
    })
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setSuccess('')
    setSubmitting(true)
    try {
      if (mode === 'signin') {
        const { error } = await supabase.auth.signInWithPassword({ email, password })
        if (error) throw error
        navigate('/')
      } else {
        const { error } = await supabase.auth.signUp({ email, password })
        if (error) throw error
        setSuccess('Account created — check your email to confirm, then sign in.')
      }
    } catch (err) {
      setError(err.message || 'Something went wrong.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center relative overflow-hidden">
      <div className="grain" />
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="relative z-[1] w-full max-w-[400px] bg-white border border-ink p-9.5"
      >
        <div className="flex items-baseline gap-0.5 text-[19px] font-semibold mb-9">
          <span className="serif font-semibold">Scout</span>
          <span className="text-[10px] font-bold tracking-wider bg-ink text-lime px-1.5 py-0.5 rounded -translate-y-1.5 ml-2 font-sans">
            AI
          </span>
        </div>

        <h1 className="serif text-[26px] font-semibold mb-1.5">
          {mode === 'signin' ? 'Welcome back' : 'Create an account'}
        </h1>
        <p className="text-sage text-[13.5px] mb-6.5 leading-relaxed">
          {mode === 'signin'
            ? 'Sign in to see your resume-ranking history.'
            : 'Sign up to start ranking and save your history.'}
        </p>

        <button
          onClick={handleGoogle}
          className="w-full py-2.5 bg-white text-ink border border-line-2 rounded-full text-[13.5px] font-semibold flex items-center justify-center gap-2 hover:border-ink hover:bg-paper-2 transition-colors"
        >
          <svg width="16" height="16" viewBox="0 0 24 24">
            <path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5c-.3 1.5-1.1 2.7-2.4 3.6v3h3.9c2.3-2.1 3.5-5.2 3.5-8.8z" />
            <path fill="#34A853" d="M12 24c3.2 0 5.9-1.1 7.9-2.9l-3.9-3c-1.1.7-2.4 1.1-4 1.1-3.1 0-5.7-2.1-6.6-4.9H1.4v3.1C3.4 21.3 7.4 24 12 24z" />
            <path fill="#FBBC05" d="M5.4 14.3c-.2-.7-.4-1.5-.4-2.3s.1-1.6.4-2.3V6.6H1.4C.5 8.3 0 10.1 0 12s.5 3.7 1.4 5.4l4-3.1z" />
            <path fill="#EA4335" d="M12 4.8c1.7 0 3.3.6 4.5 1.8l3.4-3.4C17.9 1.2 15.2 0 12 0 7.4 0 3.4 2.7 1.4 6.6l4 3.1c.9-2.8 3.5-4.9 6.6-4.9z" />
          </svg>
          Continue with Google
        </button>

        <div className="flex items-center gap-2.5 my-5.5 text-sage-2 text-[11.5px]">
          <div className="flex-1 h-px bg-line" />
          or
          <div className="flex-1 h-px bg-line" />
        </div>

        <form onSubmit={handleSubmit}>
          <label className="block text-xs font-semibold mb-1.5 mt-4">Email</label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            className="w-full px-3.5 py-2.5 border border-line-2 text-sm bg-paper focus:outline-none focus:border-ink focus:bg-white transition-colors"
          />
          <label className="block text-xs font-semibold mb-1.5 mt-4">Password</label>
          <input
            type="password"
            required
            minLength={6}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            className="w-full px-3.5 py-2.5 border border-line-2 text-sm bg-paper focus:outline-none focus:border-ink focus:bg-white transition-colors"
          />

          <button
            type="submit"
            disabled={submitting}
            className="w-full mt-5.5 py-3.5 bg-ink text-lime rounded-full text-sm font-bold hover:-translate-y-px hover:bg-[#141B16] transition-all disabled:opacity-50 disabled:hover:translate-y-0"
          >
            {submitting ? '…' : mode === 'signin' ? 'Sign in' : 'Sign up'}
          </button>

          {error && (
            <div className="text-red text-[12.5px] mt-3 px-3 py-2.5 bg-red-bg border-l-2 border-red">
              {error}
            </div>
          )}
          {success && (
            <div className="text-green-ok text-[12.5px] mt-3 px-3 py-2.5 bg-green-ok-bg border-l-2 border-green-ok">
              {success}
            </div>
          )}
        </form>

        <div className="text-center mt-5.5 text-[13px] text-sage">
          {mode === 'signin' ? "Don't have an account? " : 'Already have an account? '}
          <a onClick={toggleMode} className="text-ink font-semibold underline underline-offset-2 cursor-pointer">
            {mode === 'signin' ? 'Sign up' : 'Sign in'}
          </a>
        </div>
      </motion.div>
    </div>
  )
}
