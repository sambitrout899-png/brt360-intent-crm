import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './themes.css'
import './workspace.css'
import './layoutPrefs'
import App from './App.tsx'
import { applyOrganizationTheme } from './theme'

// open in the organization's theme from the last visit; its branding confirms it after sign-in
applyOrganizationTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
