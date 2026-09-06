import { FormatPicker } from './components/FormatPicker'
import { ProblemView } from './components/ProblemView'
import { ResultsScreen } from './components/ResultsScreen'
import { SetupScreen } from './components/SetupScreen'
import { useStore } from './store'

export default function App() {
  const screen = useStore((s) => s.screen)
  const go = useStore((s) => s.go)
  const startSession = useStore((s) => s.startSession)

  switch (screen) {
    case 'setup':
      return <SetupScreen onReady={() => go('picker')} />
    case 'picker':
      return <FormatPicker onStart={startSession} />
    case 'problem':
      return <ProblemView />
    case 'results':
      return <ResultsScreen />
  }
}
