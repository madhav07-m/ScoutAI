import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AuthProvider } from './hooks/useAuth'
import RequireAuth from './components/RequireAuth'
import Dashboard from './pages/Dashboard'
import Login from './pages/Login'
import Companies from './pages/Companies'

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <Dashboard />
              </RequireAuth>
            }
          />
          {/* Companies has no auth guard, matching the original companies.html */}
          <Route path="/companies" element={<Companies />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
