import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

// No StrictMode: it double-invokes effects, and monaco-vscode-api cannot be
// initialised twice in one document -- doing so mounts two editors and floods
// the console with "Element already has context attribute". Verified in a
// browser: with StrictMode there were 2 .monaco-editor textareas, not 1.
ReactDOM.createRoot(document.getElementById('root')!).render(<App />)
