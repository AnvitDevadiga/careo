import { Architecture } from "./components/Architecture";
import { Benchmarks } from "./components/bench/Benchmarks";
import { Demo } from "./components/demo/Demo";
import { Footer } from "./components/Footer";
import { Header } from "./components/Header";
import { Hero } from "./components/Hero";
import { HowItWorks } from "./components/HowItWorks";
import { Limitations } from "./components/Limitations";
import { QuickStart } from "./components/QuickStart";

export function App() {
  return (
    <>
      <Header />
      <main id="main" tabIndex={-1}>
        <Hero />
        <HowItWorks />
        <Demo />
        <Benchmarks />
        <QuickStart />
        <Architecture />
        <Limitations />
      </main>
      <Footer />
    </>
  );
}
