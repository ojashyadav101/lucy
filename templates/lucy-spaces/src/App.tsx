import ErrorBoundary from "./components/ErrorBoundary";
import { Calculator } from "./components/Calculator";
import { ThemeProvider } from "./contexts/ThemeContext";

function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider defaultTheme="light" switchable={false}>
        <Calculator />
      </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
