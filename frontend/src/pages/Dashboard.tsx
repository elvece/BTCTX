import React, { useEffect, useState } from 'react';
import '../styles/dashboard.css';

/**
 * A flexible interface for storing the data returned by the /api/accounts/balances endpoint.
 * Each object has an account_id, name, currency, and balance, which might be a string or number.
 */
interface AccountBalance {
  account_id: number;
  name: string;
  currency: string;
  balance: number | string; // We'll parse if it's a string
}

const Dashboard: React.FC = () => {
  /**
   * State to hold all fetched balances. Null means we're still loading or had an error.
   */
  const [balances, setBalances] = useState<AccountBalance[] | null>(null);

  /**
   * Individual balances for special accounts (Bank USD, Exchange USD/BTC, Wallet BTC).
   * We'll compute these after fetching all balances from the API.
   */
  const [bankBalance, setBankBalance] = useState<number>(0);
  const [exchangeUSDBalance, setExchangeUSDBalance] = useState<number>(0);
  const [exchangeBTCBalance, setExchangeBTCBalance] = useState<number>(0);
  const [walletBTCBalance, setWalletBTCBalance] = useState<number>(0);

  /**
   * Totals for the top-left portfolio card:
   * totalBTC => sum of all BTC across accounts
   * totalUSD => sum of all USD across accounts
   */
  const [totalBTC, setTotalBTC] = useState<number>(0);
  const [totalUSD, setTotalUSD] = useState<number>(0);

  /**
   * Track an error message in case something goes wrong during the fetch.
   * If set, we'll display a user-friendly error.
   */
  const [fetchError, setFetchError] = useState<string | null>(null);

  /**
   * 1) Fetch data on mount (component initialization).
   * We call the /api/accounts/balances endpoint to retrieve an array of balances.
   */
  useEffect(() => {
    // Adjust the URL if your API is running on a different port or path.
    fetch('http://localhost:8000/api/accounts/balances')
      .then((res) => {
        if (!res.ok) {
          // e.g., 404 or 500
          throw new Error(`HTTP error! status: ${res.status}`);
        }
        return res.json();
      })
      .then((data: unknown) => {
        console.log('Fetched balances data:', data);
        // We expect data to be an array of objects with shape { account_id, name, currency, balance }
        if (!Array.isArray(data)) {
          throw new Error('Data is not an array. Received: ' + JSON.stringify(data));
        }
        setBalances(data as AccountBalance[]);
      })
      .catch((err) => {
        console.error('Error fetching balances:', err);
        setFetchError(String(err));
      });
  }, []);

  /**
   * 2) Once balances change, parse them to find each special account's balance
   * (Bank, Exchange USD/BTC, Wallet). Also compute total BTC and total USD.
   */
  useEffect(() => {
    if (!balances) return; // If still null, skip

    let bank = 0;
    let exchUSD = 0;
    let exchBTC = 0;
    let walletBTC = 0;

    let totalBtcTemp = 0;
    let totalUsdTemp = 0;

    balances.forEach((acc) => {
      // Convert balance to a number if it’s a string
      const numericBalance =
        typeof acc.balance === 'string' ? parseFloat(acc.balance) : acc.balance;

      if (Number.isNaN(numericBalance)) {
        console.warn('Encountered NaN balance for account:', acc);
        return; // Skip this account if balance parsing failed
      }

      // Identify special accounts by name + currency
      if (acc.name === 'Bank' && acc.currency === 'USD') {
        bank = numericBalance;
      } else if (acc.name === 'Wallet' && acc.currency === 'BTC') {
        walletBTC = numericBalance;
      } else if (acc.name === 'Exchange USD' && acc.currency === 'USD') {
        exchUSD = numericBalance;
      } else if (acc.name === 'Exchange BTC' && acc.currency === 'BTC') {
        exchBTC = numericBalance;
      }

      // Also accumulate total BTC / total USD for the top-level portfolio
      if (acc.currency === 'BTC') {
        totalBtcTemp += numericBalance;
      } else if (acc.currency === 'USD') {
        totalUsdTemp += numericBalance;
      }
    });

    // Update state with the computed balances
    setBankBalance(bank);
    setExchangeUSDBalance(exchUSD);
    setExchangeBTCBalance(exchBTC);
    setWalletBTCBalance(walletBTC);

    setTotalBTC(totalBtcTemp);
    setTotalUSD(totalUsdTemp);
  }, [balances]);

  /**
   * Simple placeholders for fields we haven't implemented yet (like unrealized gains or a chart).
   * We'll display these placeholders in the Portfolio card.
   */
  const unrealizedGains = 0;
  const portfolioChartPlaceholder = '(Chart Placeholder)';

  /**
   * If there was a fetch error, display a user-friendly error message.
   */
  if (fetchError) {
    return (
      <div style={{ color: 'red', margin: '2rem' }}>
        <h2>Unable to load balances</h2>
        <p>{fetchError}</p>
      </div>
    );
  }

  /**
   * If balances are still null, we're in a loading state.
   */
  if (balances === null) {
    return (
      <div className="dashboard">
        <h2>Loading balances...</h2>
      </div>
    );
  }

  /**
   * Otherwise, render the main Dashboard layout.
   * We preserve your existing top row (Portfolio, Bitcoin Price)
   * and the bottom row (Bank, Exchange, Wallet).
   * Then we add three new containers below them for Gains and Losses.
   */
  return (
    <div className="dashboard">
      {/* Top row with two cards side by side */}
      <div className="dashboard-row top-row">
        {/* Left card: Portfolio Summary */}
        <div className="card">
          <h5>Portfolio</h5>
          <p>BTC Balance: {totalBTC.toFixed(4)} BTC</p>
          <p>USD Value: ${totalUSD.toFixed(2)}</p>
          <p>Unrealized Gains/Losses: {unrealizedGains}</p>
          <p>Portfolio Chart: {portfolioChartPlaceholder}</p>
        </div>

        {/* Right card: Bitcoin Price placeholder */}
        <div className="card">
          <h5>Bitcoin Price</h5>
          <p>Placeholder for live BTC price & chart</p>
        </div>
      </div>

      {/* Bottom row with three cards side by side (existing layout) */}
      <div className="dashboard-row bottom-row">
        {/* Bank card */}
        <div className="card">
          <h5>Bank</h5>
          <p>USD Balance: ${bankBalance.toFixed(2)}</p>
        </div>

        {/* Exchange card */}
        <div className="card">
          <h5>Exchange</h5>
          <p>USD Balance: ${exchangeUSDBalance.toFixed(2)}</p>
          <p>BTC Balance: {exchangeBTCBalance.toFixed(4)} BTC</p>
        </div>

        {/* Wallet card */}
        <div className="card">
          <h5>Wallet</h5>
          <p>BTC Balance: {walletBTCBalance.toFixed(4)} BTC</p>
        </div>
      </div>

      {/* NEW ROW: three more containers underneath Bank, Exchange, and Wallet */}
      <div className="dashboard-row bottom-row">
        {/* Container 1: Realized Gains (under Bank) */}
        <div className="card">
          <h5>Realized Gains</h5>
          <p>Short term: (Placeholder)</p>
          <p>Long term: (Placeholder)</p>
          <p>Total: (Placeholder)</p>
        </div>

        {/* Container 2: Other Gains (under Exchange) */}
        <div className="card">
          <h5>Other Gains</h5>
          <p>Income: (Placeholder)</p>
          <p>Interest: (Placeholder)</p>
          <p>Spent: (Placeholder)</p>
        </div>

        {/* Container 3: Losses (under Wallet) */}
        <div className="card">
          <h5>Losses</h5>
          <p>Short term: (Placeholder)</p>
          <p>Long term: (Placeholder)</p>
          <p>Total: (Placeholder)</p>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
