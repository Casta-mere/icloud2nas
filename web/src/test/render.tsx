import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";
import { StreamContext } from "../events/StreamContext";
import { I18nProvider, type Lang } from "../i18n";

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } },
  });
}

export interface RenderOptions {
  route?: string;
  lang?: Lang;
  /** What the stream context reports (pages read it; `App` mounts the real stream). */
  connected?: boolean;
}

export function renderWithProviders(
  ui: ReactElement,
  { route = "/", lang = "zh", connected = true }: RenderOptions = {},
): RenderResult & { queryClient: QueryClient; user: UserEvent } {
  localStorage.setItem("i2n.lang", lang);
  const queryClient = makeQueryClient();
  const user = userEvent.setup();
  const result = render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <MemoryRouter initialEntries={[route]}>
          <StreamContext.Provider value={{ connected }}>{ui}</StreamContext.Provider>
        </MemoryRouter>
      </I18nProvider>
    </QueryClientProvider>,
  );
  return { ...result, queryClient, user };
}
