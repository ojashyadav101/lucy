import { useState, useEffect } from 'react'
import { ArrowRightLeft, TrendingUp, DollarSign, Euro, JapaneseYen, PoundSterling, SwissFranc, CanadianFlag, AustralianFlag, Bitcoin } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { motion, AnimatePresence } from 'framer-motion'

interface Currency {
  code: string
  name: string
  symbol: string
  flag: string
  rate: number
}

const currencies: Currency[] = [
  { code: 'USD', name: 'US Dollar', symbol: '$', flag: '🇺🇸', rate: 1 },
  { code: 'EUR', name: 'Euro', symbol: '€', flag: '🇪🇺', rate: 0.92 },
  { code: 'GBP', name: 'British Pound', symbol: '£', flag: '🇬🇧', rate: 0.79 },
  { code: 'JPY', name: 'Japanese Yen', symbol: '¥', flag: '🇯🇵', rate: 150.14 },
  { code: 'AUD', name: 'Australian Dollar', symbol: 'A$', flag: '🇦🇺', rate: 1.53 },
  { code: 'CAD', name: 'Canadian Dollar', symbol: 'C$', flag: '🇨🇦', rate: 1.35 },
  { code: 'CHF', name: 'Swiss Franc', symbol: 'Fr', flag: '🇨🇭', rate: 0.88 },
  { code: 'CNY', name: 'Chinese Yuan', symbol: '¥', flag: '🇨🇳', rate: 7.19 },
  { code: 'INR', name: 'Indian Rupee', symbol: '₹', flag: '🇮🇳', rate: 82.90 },
  { code: 'SGD', name: 'Singapore Dollar', symbol: 'S$', flag: '🇸🇬', rate: 1.34 },
]

const getCurrencyIcon = (code: string) => {
  switch (code) {
    case 'USD': return <DollarSign className="w-4 h-4" />
    case 'EUR': return <Euro className="w-4 h-4" />
    case 'GBP': return <PoundSterling className="w-4 h-4" />
    case 'JPY': return <JapaneseYen className="w-4 h-4" />
    case 'CHF': return <SwissFranc className="w-4 h-4" />
    default: return <span className="text-sm font-bold">{code[0]}</span>
  }
}

export default function App() {
  const [amount, setAmount] = useState<string>('1000')
  const [fromCurrency, setFromCurrency] = useState<Currency>(currencies[0])
  const [toCurrency, setToCurrency] = useState<Currency>(currencies[1])
  const [convertedAmount, setConvertedAmount] = useState<number>(0)
  const [isSwapping, setIsSwapping] = useState(false)
  const [showFromDropdown, setShowFromDropdown] = useState(false)
  const [showToDropdown, setShowToDropdown] = useState(false)

  useEffect(() => {
    const numAmount = parseFloat(amount) || 0
    const rate = toCurrency.rate / fromCurrency.rate
    setConvertedAmount(numAmount * rate)
  }, [amount, fromCurrency, toCurrency])

  const handleSwap = () => {
    setIsSwapping(true)
    setTimeout(() => {
      setFromCurrency(toCurrency)
      setToCurrency(fromCurrency)
      setIsSwapping(false)
    }, 200)
  }

  const formatNumber = (num: number, currency: Currency) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency.code,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(num)
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900 flex items-center justify-center p-4">
      {/* Background decoration */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-20 left-10 w-72 h-72 bg-purple-500/20 rounded-full blur-3xl" />
        <div className="absolute bottom-20 right-10 w-96 h-96 bg-blue-500/20 rounded-full blur-3xl" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-pink-500/10 rounded-full blur-3xl" />
      </div>

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease: 'easeOut' }}
        className="relative w-full max-w-md"
      >
        {/* Glass card */}
        <div className="backdrop-blur-xl bg-white/10 border border-white/20 rounded-3xl p-8 shadow-2xl">
          {/* Header */}
          <div className="text-center mb-8">
            <motion.div
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={{ delay: 0.2, type: 'spring', stiffness: 200 }}
              className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-purple-500 to-pink-500 mb-4 shadow-lg shadow-purple-500/25"
            >
              <ArrowRightLeft className="w-8 h-8 text-white" />
            </motion.div>
            <h1 className="text-3xl font-bold text-white mb-2">Currency Exchange</h1>
            <p className="text-white/60 text-sm">Real-time conversion rates</p>
          </div>

          {/* Amount Input */}
          <div className="mb-6">
            <label className="block text-white/70 text-sm font-medium mb-2 ml-1">Amount</label>
            <div className="relative">
              <Input
                type="number"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                className="w-full bg-white/5 border-white/10 text-white text-2xl font-semibold h-16 pl-6 pr-4 rounded-2xl focus:border-purple-500/50 focus:ring-purple-500/20 placeholder:text-white/30"
                placeholder="0.00"
              />
            </div>
          </div>

          {/* Currency Selection */}
          <div className="space-y-4 mb-6">
            {/* From Currency */}
            <motion.div
              layout
              className="bg-white/5 border border-white/10 rounded-2xl p-4 relative"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-purple-500/20 to-pink-500/20 flex items-center justify-center border border-white/10">
                    <span className="text-xl">{fromCurrency.flag}</span>
                  </div>
                  <div>
                    <p className="text-white font-semibold">{fromCurrency.code}</p>
                    <p className="text-white/50 text-sm">{fromCurrency.name}</p>
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setShowFromDropdown(!showFromDropdown)
                    setShowToDropdown(false)
                  }}
                  className="text-white/70 hover:text-white hover:bg-white/10"
                >
                  Change
                </Button>
              </div>

              <AnimatePresence>
                {showFromDropdown && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    exit={{ opacity: 0, height: 0 }}
                    className="overflow-hidden"
                  >
                    <div className="pt-4 grid grid-cols-2 gap-2">
                      {currencies.map((curr) => (
                        <button
                          key={curr.code}
                          onClick={() => {
                            setFromCurrency(curr)
                            setShowFromDropdown(false)
                          }}
                          className={`flex items-center gap-2 p-2 rounded-xl text-left transition-all ${
                            fromCurrency.code === curr.code
                              ? 'bg-purple-500/30 border border-purple-500/50'
                              : 'hover:bg-white/5 border border-transparent'
                          }`}
                        >
                          <span className="text-lg">{curr.flag}</span>
                          <span className="text-white text-sm font-medium">{curr.code}</span>
                        </button>
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>

            {/* Swap Button */}
            <div className="flex justify-center -my-2 relative z-10">
              <motion.button
                whileHover={{ scale: 1.1, rotate: 180 }}
                whileTap={{ scale: 0.9 }}
                onClick={handleSwap}
                disabled={isSwapping}
                className="w-12 h-12 rounded-full bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center shadow-lg shadow-purple-500/25 border-4 border-slate-900/50"
              >
                <ArrowRightLeft className="w-5 h-5 text-white" />
              </motion.button>
            </div>

            {/* To Currency */}
            <motion.div
              layout
              className="bg-white/5 border border-white/10 rounded-2xl p-4 relative"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-pink-500/20 to-purple-500/20 flex items-center justify-center border border-white/10">
                    <span className="text-xl">{toCurrency.flag}</span>
                  </div>
                  <div>
                    <p className="text-white font-semibold">{toCurrency.code}</p>
                    <p className="text-white/50 text-sm">{toCurrency.name}</p>
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setShowToDropdown(!showToDropdown)
                    setShowFromDropdown(false)
                  }}
                  className="text-white/70 hover:text-white hover:bg-white/10"
                >
                  Change
                </Button>
              </div>

              <AnimatePresence>
                {showToDropdown && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    exit={{ opacity: 0, height: 0 }}
                    className="overflow-hidden"
                  >
                    <div className="pt-4 grid grid-cols-2 gap-2">
                      {currencies.map((curr) => (
                        <button
                          key={curr.code}
                          onClick={() => {
                            setToCurrency(curr)
                            setShowToDropdown(false)
                          }}
                          className={`flex items-center gap-2 p-2 rounded-xl text-left transition-all ${
                            toCurrency.code === curr.code
                              ? 'bg-pink-500/30 border border-pink-500/50'
                              : 'hover:bg-white/5 border border-transparent'
                          }`}
                        >
                          <span className="text-lg">{curr.flag}</span>
                          <span className="text-white text-sm font-medium">{curr.code}</span>
                        </button>
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          </div>

          {/* Result */}
          <motion.div
            key={convertedAmount}
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            className="bg-gradient-to-r from-purple-500/20 to-pink-500/20 border border-white/10 rounded-2xl p-6 text-center"
          >
            <p className="text-white/60 text-sm mb-1">Converted Amount</p>
            <p className="text-4xl font-bold text-white tracking-tight">
              {formatNumber(convertedAmount, toCurrency)}
            </p>
            <div className="flex items-center justify-center gap-2 mt-3 text-white/50 text-sm">
              <TrendingUp className="w-4 h-4 text-green-400" />
              <span>1 {fromCurrency.code} = {(toCurrency.rate / fromCurrency.rate).toFixed(4)} {toCurrency.code}</span>
            </div>
          </motion.div>

          {/* Footer */}
          <div className="mt-6 text-center">
            <p className="text-white/30 text-xs">
              Rates are for demonstration purposes
            </p>
          </div>
        </div>

        {/* Quick Stats */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.4 }}
          className="mt-6 grid grid-cols-3 gap-3"
        >
          {[
            { label: 'Active Currencies', value: currencies.length },
            { label: 'Updates', value: 'Real-time' },
            { label: 'Precision', value: '4 Decimals' },
          ].map((stat, i) => (
            <div key={i} className="backdrop-blur-md bg-white/5 border border-white/10 rounded-xl p-3 text-center">
              <p className="text-lg font-bold text-white">{stat.value}</p>
              <p className="text-white/40 text-xs">{stat.label}</p>
            </div>
          ))}
        </motion.div>
      </motion.div>
    </div>
  )
}
