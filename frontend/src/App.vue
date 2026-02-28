<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
import CurrentPower from './components/CurrentPower.vue'
import TodaySummary from './components/TodaySummary.vue'
import ProductionChart from './components/ProductionChart.vue'

const current     = ref(null)
const today       = ref(null)
const week        = ref(null)
const chart       = ref(null)
const forecast    = ref(null)
const updatedAt   = ref(null)
const batteryKwh  = ref(8)

const batteryOptions = [
  { label: 'No battery', value: 0 },
  { label: '1 kWh',      value: 1 },
  { label: '2 kWh',      value: 2 },
  { label: '4 kWh',      value: 4 },
  { label: '6 kWh',      value: 6 },
  { label: '8 kWh',      value: 8 },
  { label: '10 kWh',     value: 10 },
  { label: '12 kWh',     value: 12 },
  { label: '16 kWh',     value: 16 },
  { label: '20 kWh',     value: 20 },
  { label: '30 kWh',     value: 30 },
]

async function fetchAll() {
  const bq = `?battery_kwh=${batteryKwh.value}`
  const [c, t, w, ch, fc] = await Promise.all([
    fetch(`/api/current?battery_kwh=${batteryKwh.value}`).then(r => r.json()),
    fetch(`/api/today${bq}`).then(r => r.json()),
    fetch(`/api/week${bq}`).then(r => r.json()),
    fetch(`/api/today/chart${bq}`).then(r => r.json()),
    fetch('/api/forecast').then(r => r.json()),
  ])
  current.value  = c
  today.value    = t
  week.value     = w
  chart.value    = ch.points
  forecast.value = fc.hours
  updatedAt.value = new Date().toLocaleTimeString('nl-NL')
}

watch(batteryKwh, fetchAll)

let timer
onMounted(() => {
  fetchAll()
  timer = setInterval(fetchAll, 60_000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="min-h-screen md:h-screen bg-gray-950 text-white flex flex-col p-4 gap-4 md:overflow-hidden">

    <!-- Header -->
    <div class="flex-shrink-0 flex items-center justify-between">
      <h1 class="text-xl font-semibold tracking-tight">⚡ Energy</h1>
      <div class="flex items-center gap-3">
        <select
          v-model.number="batteryKwh"
          class="text-xs bg-gray-900 text-gray-400 border border-gray-800 rounded-lg px-2 py-1 focus:outline-none"
        >
          <option v-for="o in batteryOptions" :key="o.value" :value="o.value">🔋 {{ o.label }}</option>
        </select>
        <span v-if="updatedAt" class="text-xs text-gray-600">{{ updatedAt }}</span>
        <button
          @click="fetchAll"
          class="text-xs text-gray-500 hover:text-white transition-colors"
        >↻ refresh</button>
      </div>
    </div>

    <!-- Content: stacked column that fills remaining viewport height -->
    <div class="md:flex-1 md:min-h-0 flex flex-col gap-4">
      <CurrentPower :data="current" :forecast="forecast" />
      <div class="md:flex-1 md:min-h-0 overflow-hidden flex flex-col">
        <ProductionChart :points="chart" />
      </div>
      <div class="flex-shrink-0 grid grid-cols-2 gap-4">
        <TodaySummary :data="today" title="Today" />
        <TodaySummary :data="week" title="This week" />
      </div>
    </div>
  </div>
</template>
