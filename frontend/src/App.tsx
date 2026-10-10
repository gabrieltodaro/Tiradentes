import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import Home from "./pages/Home";

export default function App(): JSX.Element {
  // Create once to preserve cache between re-renders.
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          mutations: { retry: 0 },
          queries: { retry: 1, refetchOnWindowFocus: false }
        }
      })
  );

  return (
    <QueryClientProvider client={queryClient}>
      <Home />
    </QueryClientProvider>
  );
}
