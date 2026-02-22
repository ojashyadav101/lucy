import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export function Calculator() {
  const [display, setDisplay] = useState("0");
  const [previousValue, setPreviousValue] = useState<string | null>(null);
  const [operation, setOperation] = useState<string | null>(null);
  const [resetNext, setResetNext] = useState(false);

  const handleNumber = (num: string) => {
    if (resetNext) {
      setDisplay(num);
      setResetNext(false);
    } else {
      setDisplay(display === "0" ? num : display + num);
    }
  };

  const handleDecimal = () => {
    if (resetNext) {
      setDisplay("0.");
      setResetNext(false);
      return;
    }
    if (!display.includes(".")) {
      setDisplay(display + ".");
    }
  };

  const handleOperation = (op: string) => {
    if (previousValue && operation && !resetNext) {
      const result = calculate(Number(previousValue), Number(display), operation);
      setDisplay(String(result));
      setPreviousValue(String(result));
    } else {
      setPreviousValue(display);
    }
    setOperation(op);
    setResetNext(true);
  };

  const calculate = (a: number, b: number, op: string): number => {
    switch (op) {
      case "+": return a + b;
      case "−": return a - b;
      case "×": return a * b;
      case "÷": return b !== 0 ? a / b : 0;
      default: return b;
    }
  };

  const handleEquals = () => {
    if (!previousValue || !operation) return;
    const result = calculate(Number(previousValue), Number(display), operation);
    const formatted = Number.isInteger(result) ? String(result) : parseFloat(result.toFixed(10)).toString();
    setDisplay(formatted);
    setPreviousValue(null);
    setOperation(null);
    setResetNext(true);
  };

  const handleClear = () => {
    setDisplay("0");
    setPreviousValue(null);
    setOperation(null);
    setResetNext(false);
  };

  const handlePercent = () => {
    setDisplay(String(Number(display) / 100));
    setResetNext(true);
  };

  const handleToggleSign = () => {
    setDisplay(String(Number(display) * -1));
  };

  const formatDisplay = (val: string) => {
    if (val.includes(".") && !val.endsWith(".")) {
      const [intPart, decPart] = val.split(".");
      return `${Number(intPart).toLocaleString()}.${decPart}`;
    }
    if (val.endsWith(".")) {
      return `${Number(val.slice(0, -1)).toLocaleString()}.`;
    }
    return Number(val).toLocaleString();
  };

  const displayFontSize = display.length > 12 ? "text-2xl" : display.length > 8 ? "text-3xl" : "text-4xl";

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-gradient-to-br from-background to-muted/50">
      <Card className="w-full max-w-xs shadow-2xl border-border/50 overflow-hidden">
        {/* Display */}
        <div className="p-5 pb-3">
          <div className="text-right min-h-[2rem] mb-1">
            {previousValue && operation && (
              <span className="text-sm text-muted-foreground font-mono">
                {formatDisplay(previousValue)} {operation}
              </span>
            )}
          </div>
          <div
            className={`text-right font-semibold font-mono tracking-tight ${displayFontSize} transition-all truncate`}
            data-testid="display"
          >
            {formatDisplay(display)}
          </div>
        </div>

        {/* Buttons */}
        <div className="grid grid-cols-4 gap-[1px] bg-border/30 p-[1px]">
          {/* Row 1 */}
          <CalcButton label="C" onClick={handleClear} variant="secondary" />
          <CalcButton label="±" onClick={handleToggleSign} variant="secondary" />
          <CalcButton label="%" onClick={handlePercent} variant="secondary" />
          <CalcButton label="÷" onClick={() => handleOperation("÷")} variant="accent" active={operation === "÷" && resetNext} />

          {/* Row 2 */}
          <CalcButton label="7" onClick={() => handleNumber("7")} />
          <CalcButton label="8" onClick={() => handleNumber("8")} />
          <CalcButton label="9" onClick={() => handleNumber("9")} />
          <CalcButton label="×" onClick={() => handleOperation("×")} variant="accent" active={operation === "×" && resetNext} />

          {/* Row 3 */}
          <CalcButton label="4" onClick={() => handleNumber("4")} />
          <CalcButton label="5" onClick={() => handleNumber("5")} />
          <CalcButton label="6" onClick={() => handleNumber("6")} />
          <CalcButton label="−" onClick={() => handleOperation("−")} variant="accent" active={operation === "−" && resetNext} />

          {/* Row 4 */}
          <CalcButton label="1" onClick={() => handleNumber("1")} />
          <CalcButton label="2" onClick={() => handleNumber("2")} />
          <CalcButton label="3" onClick={() => handleNumber("3")} />
          <CalcButton label="+" onClick={() => handleOperation("+")} variant="accent" active={operation === "+" && resetNext} />

          {/* Row 5 */}
          <CalcButton label="0" onClick={() => handleNumber("0")} wide />
          <CalcButton label="." onClick={handleDecimal} />
          <CalcButton label="=" onClick={handleEquals} variant="accent" />
        </div>
      </Card>
    </div>
  );
}

function CalcButton({
  label,
  onClick,
  variant = "default",
  wide = false,
  active = false,
}: {
  label: string;
  onClick: () => void;
  variant?: "default" | "secondary" | "accent";
  wide?: boolean;
  active?: boolean;
}) {
  const baseClasses = "h-14 text-lg font-medium rounded-none transition-all active:scale-95 focus-visible:z-10";

  const variantClasses = {
    default: "bg-background hover:bg-muted/80 text-foreground",
    secondary: "bg-muted/60 hover:bg-muted text-foreground",
    accent: active
      ? "bg-primary-foreground text-primary hover:bg-primary-foreground/90"
      : "bg-primary hover:bg-primary/90 text-primary-foreground",
  };

  return (
    <Button
      variant="ghost"
      className={`${baseClasses} ${variantClasses[variant]} ${wide ? "col-span-2" : ""}`}
      onClick={onClick}
      data-testid={`btn-${label}`}
    >
      {label}
    </Button>
  );
}
