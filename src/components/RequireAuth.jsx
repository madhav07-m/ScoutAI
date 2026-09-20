import { Navigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

export default function RequireAuth({ children }) {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-paper text-sage text-sm">
        Loading…
      </div>
    )
  }

  if (!user) return <Navigate to="/login" replace />

  return children
}
