<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.budgetLabel') }}</h3>
        </div>
        <div class="budget-value">{{ formatCurrency(budget, currentCurrency) }}</div>
        <input
          class="budget-slider"
          type="range"
          v-model.number="budget"
          :min="0"
          :max="sliderMax"
          :step="sliderStep"
        >
        <div class="slider-labels">
          <span>{{ formatCurrency(0, currentCurrency) }}</span>
          <span>{{ formatCurrency(sliderMax, currentCurrency) }}</span>
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.itemsRecommended') }}</div>
          <div class="stat-value">{{ recommendations.length }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.totalCost') }}</div>
          <div class="stat-value">{{ formatCurrency(totalCost, currentCurrency) }}</div>
        </div>
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.budgetUsed') }}</div>
          <div class="stat-value">{{ budgetUsedPct }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.projectedLeadTime') }}</div>
          <div class="stat-value">
            {{ projectedLeadTime ? projectedLeadTime + ' ' + t('restocking.days') : '—' }}
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendedItems') }}</h3>
        </div>
        <div v-if="recommendations.length" class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.item') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th>{{ t('restocking.table.quantity') }}</th>
                <th>{{ t('restocking.table.unitCost') }}</th>
                <th>{{ t('restocking.table.lineTotal') }}</th>
                <th>{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in recommendations" :key="row.item_sku">
                <td><strong>{{ row.item_sku }}</strong></td>
                <td>{{ row.item_name }}</td>
                <td><span :class="['badge', row.trend]">{{ t('trends.' + row.trend) }}</span></td>
                <td>{{ row.quantity.toLocaleString() }}</td>
                <td>{{ formatCurrency(row.unit_price, currentCurrency) }}</td>
                <td>{{ formatCurrency(row.lineTotal, currentCurrency) }}</td>
                <td>{{ row.lead_time_days }} {{ t('common.days') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="empty-state">{{ t('restocking.noRecommendations') }}</div>
      </div>

      <div class="place-order-area">
        <button
          class="place-order-btn"
          :disabled="!recommendations.length || submitting"
          @click="placeOrder"
        >
          {{ submitting ? t('restocking.placingOrder') : t('restocking.placeOrder') }}
        </button>
      </div>

      <div v-if="placedOrder" class="card success-panel">
        <h3 class="card-title">{{ t('restocking.orderPlaced') }}</h3>
        <p>
          {{ t('restocking.orderPlacedDetail', {
            orderNumber: placedOrder.order_number,
            leadTime: placedOrder.lead_time_days
          }) }}
        </p>
        <router-link to="/orders" class="view-orders-link">
          {{ t('restocking.viewInOrders') }}
        </router-link>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency } from '../utils/currency'

export default {
  name: 'Restocking',
  setup() {
    // Note: this view intentionally ignores the global FilterBar — demand
    // forecasts have no warehouse/period dimension and /api/demand is unfiltered.
    const { t, currentCurrency } = useI18n()

    const loading = ref(true)
    const error = ref(null)
    const allForecasts = ref([])
    const budget = ref(0)
    const submitting = ref(false)
    const placedOrder = ref(null)

    const COVERAGE_MULTIPLIER = 1.5
    const TREND_WEIGHT = { increasing: 1.5, stable: 1.0, decreasing: 0.5 }
    const sliderStep = 1000

    // Coverage target: stock up to 1.5x the forecast, minus what current demand already covers.
    const coverageQty = (f) =>
      Math.max(Math.round(COVERAGE_MULTIPLIER * f.forecasted_demand) - f.current_demand, 0)

    const fullRestockCost = computed(() =>
      allForecasts.value.reduce((sum, f) => sum + coverageQty(f) * f.unit_cost, 0)
    )

    const sliderMax = computed(() =>
      Math.max(10000, Math.ceil(fullRestockCost.value / 10000) * 10000)
    )

    // Greedy fill: rank by urgency (relative shortfall x trend weight), then spend
    // the budget on the most urgent items until it runs out.
    const recommendations = computed(() => {
      const ranked = allForecasts.value
        .map(f => ({
          f,
          qtyWanted: coverageQty(f),
          urgency: (coverageQty(f) / Math.max(f.current_demand, 1)) * (TREND_WEIGHT[f.trend] || 1)
        }))
        .filter(x => x.qtyWanted > 0)
        .sort((a, b) => b.urgency - a.urgency)

      let remaining = budget.value
      const rows = []
      for (const { f, qtyWanted } of ranked) {
        const affordable = Math.floor(remaining / f.unit_cost)
        const qty = Math.min(qtyWanted, affordable)
        if (qty > 0) {
          const lineTotal = qty * f.unit_cost
          rows.push({
            item_sku: f.item_sku,
            item_name: f.item_name,
            trend: f.trend,
            quantity: qty,
            unit_price: f.unit_cost,
            lineTotal,
            lead_time_days: f.lead_time_days
          })
          remaining -= lineTotal
        }
      }
      return rows
    })

    const totalCost = computed(() =>
      recommendations.value.reduce((s, r) => s + r.lineTotal, 0)
    )

    const budgetUsedPct = computed(() =>
      budget.value > 0 ? Math.round((totalCost.value / budget.value) * 100) + '%' : '0%'
    )

    const projectedLeadTime = computed(() =>
      recommendations.value.length
        ? Math.max(...recommendations.value.map(r => r.lead_time_days))
        : 0
    )

    const loadForecasts = async () => {
      try {
        loading.value = true
        error.value = null
        allForecasts.value = await api.getDemandForecasts()
        // Default the budget to roughly half of a full restock so recommendations show immediately.
        budget.value = Math.round(fullRestockCost.value / 2 / sliderStep) * sliderStep
      } catch (err) {
        error.value = 'Failed to load demand forecasts: ' + err.message
      } finally {
        loading.value = false
      }
    }

    const placeOrder = async () => {
      if (!recommendations.value.length || submitting.value) return
      try {
        submitting.value = true
        error.value = null
        const items = recommendations.value.map(r => ({
          item_sku: r.item_sku,
          item_name: r.item_name,
          quantity: r.quantity,
          unit_price: r.unit_price
        }))
        placedOrder.value = await api.createRestockOrder({ budget: budget.value, items })
      } catch (err) {
        error.value = 'Failed to place restocking order: ' + err.message
      } finally {
        submitting.value = false
      }
    }

    onMounted(loadForecasts)

    return {
      t,
      currentCurrency,
      formatCurrency,
      loading,
      error,
      budget,
      sliderMax,
      sliderStep,
      submitting,
      placedOrder,
      recommendations,
      totalCost,
      budgetUsedPct,
      projectedLeadTime,
      placeOrder
    }
  }
}
</script>

<style scoped>
.budget-value {
  font-size: 2.25rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.025em;
  margin-bottom: 1rem;
}

.budget-slider {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 6px;
  border-radius: 999px;
  background: #e2e8f0;
  outline: none;
  cursor: pointer;
}

.budget-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #2563eb;
  border: none;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25);
  cursor: pointer;
}

.budget-slider::-moz-range-thumb {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #2563eb;
  border: none;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25);
  cursor: pointer;
}

.budget-slider::-moz-range-track {
  height: 6px;
  border-radius: 999px;
  background: #e2e8f0;
}

.slider-labels {
  display: flex;
  justify-content: space-between;
  margin-top: 0.5rem;
  font-size: 0.813rem;
  color: #64748b;
}

.empty-state {
  padding: 2rem;
  text-align: center;
  color: #64748b;
  font-size: 0.938rem;
}

.place-order-area {
  margin: 1.5rem 0;
}

.place-order-btn {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: #ffffff;
  border: none;
  border-radius: 8px;
  padding: 0.75rem 1.5rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.place-order-btn:hover:not(:disabled) {
  transform: translateY(-2px);
}

.place-order-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.success-panel {
  border-color: #bbf7d0;
  background: #f0fdf4;
}

.success-panel p {
  color: #334155;
  font-size: 0.938rem;
  margin: 0.75rem 0 1rem;
}

.view-orders-link {
  color: #2563eb;
  font-weight: 600;
  text-decoration: none;
  font-size: 0.938rem;
}

.view-orders-link:hover {
  text-decoration: underline;
}
</style>
