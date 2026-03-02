import ErrorBoundary from "./components/ErrorBoundary";
import { ThemeProvider } from "./contexts/ThemeContext";

// LUCY_SPACES_PLACEHOLDER — this file must be overwritten by the agent before deploying.

function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider defaultTheme="light" switchable={false}>
        <div className="flex items-center justify-center min-h-screen">
          <p className="text-lg text-gray-400">App is loading...</p>
        </div>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
