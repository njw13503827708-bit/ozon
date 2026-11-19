import React from "react";
import ReactDOM from "react-dom/client";
import App from "@/App";
import { ProductCenter } from "@/components/erp/ProductCenter";
import { ReviewCenter } from "@/components/erp/ReviewCenter";
import { StoreCenter } from "@/components/erp/StoreCenter";
import { AnalyticsCenter } from "@/components/erp/AnalyticsCenter";
import { OptimizationWorkbench } from "@/components/erp/OptimizationWorkbench";
import { OrderCenter } from "@/components/erp/OrderCenter";
import "@/styles.css";

function Workspace() {
  const [page, setPage] = React.useState(() => window.location.hash);
  const [runtime,setRuntime]=React.useState<{mode:string;root:string;build:string}|null>(null);
  React.useEffect(()=>{let active=true;void fetch('/api/workbench/runtime-identity').then(r=>r.ok?r.json():null).then(value=>{if(active)setRuntime(value);}).catch(()=>{});return()=>{active=false;};},[]);
  React.useEffect(() => {
    const onHashChange = () => setPage(window.location.hash);
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  const isAnalytics = page.startsWith("#analytics");
  const isOptimization = page.startsWith("#optimization"), isOrders = page.startsWith("#orders");
  const content = isOptimization ? <OptimizationWorkbench /> : isOrders ? <OrderCenter /> : isAnalytics ? <AnalyticsCenter /> : page === "#stores" ? <StoreCenter /> : page.split("?")[0] === "#products" ? <ProductCenter /> : page === "#reviews" ? <ReviewCenter /> : <App />;
  return <>
    {runtime && <div role="status" className="break-all border-b border-amber-500/30 bg-slate-900 px-5 py-2 text-xs">隔离候选 · {runtime.mode} · 构建 {runtime.build} · 数据目录 {runtime.root} · 本地保存 ≠ 平台成功</div>}
    <nav aria-label="主导航" className="flex flex-wrap gap-6 border-b border-white/10 px-5 py-3 text-sm">
      <a href="#workbench" aria-current={!isAnalytics && !isOptimization && !isOrders && page !== "#stores" && page.split("?")[0] !== "#products" && page !== "#reviews" ? "page" : undefined}>工作台</a>
      <a href="#stores" aria-current={page === "#stores" ? "page" : undefined}>店铺中心</a>
      <a href="#products" aria-current={page.split("?")[0] === "#products" ? "page" : undefined}>商品中心</a>
      <a href="#analytics" aria-current={isAnalytics ? "page" : undefined}>运营分析</a>
      <a href="#reviews" aria-current={page === "#reviews" ? "page" : undefined}>评价中心</a>
      <a href="#optimization" aria-current={isOptimization ? "page" : undefined}>商品优化</a>
      <a href="#orders" aria-current={isOrders ? "page" : undefined}>订单中心</a>
    </nav>
    {content}
  </>;
}

ReactDOM.createRoot(document.getElementById("root")!).render(<Workspace />);
