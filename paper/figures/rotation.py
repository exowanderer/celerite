#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import division, print_function

import emcee
import fitsio
import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import minimize

from plot_setup import setup, SQUARE_FIGSIZE, COLORS

import genrp
from genrp import kernels

np.random.seed(42)
setup()

# Define the custom kernel
class RotationKernel(kernels.Kernel):
    parameter_names = ("log_amp", "log_timescale", "log_period", "log_factor")

    @property
    def p_real(self):
        return 1

    @property
    def p_complex(self):
        return 1

    @property
    def alpha_real(self):
        f = np.exp(self.log_factor)
        return np.array([np.exp(self.log_amp) * (1.0 + f) / (2.0 + f)])

    @property
    def beta_real(self):
        return np.array([np.exp(-self.log_timescale)])

    @property
    def alpha_complex_real(self):
        f = np.exp(self.log_factor)
        return np.array([np.exp(self.log_amp) / (2.0 + f)])

    @property
    def alpha_complex_imag(self):
        return np.array([0.0])

    @property
    def beta_complex_real(self):
        return np.array([np.exp(-self.log_timescale)])

    @property
    def beta_complex_imag(self):
        return np.array([2*np.pi*np.exp(-self.log_period)])

# Load the data
data = fitsio.read("data/kplr001430163-2013011073258_llc.fits")
# data = fitsio.read("data/kplr001430163-2010355172524_llc.fits")
m = data["SAP_QUALITY"] == 0
m &= np.isfinite(data["TIME"]) & np.isfinite(data["PDCSAP_FLUX"])
t = np.ascontiguousarray(data["TIME"][m], dtype=np.float64)
t -= np.min(t)
y = np.ascontiguousarray(data["PDCSAP_FLUX"][m], dtype=np.float64)
yerr = np.ascontiguousarray(data["PDCSAP_FLUX_ERR"][m], dtype=np.float64)

# Normalize the data
mean = np.median(y)
y = (y / mean - 1.0) * 1e3
yerr *= 1e3 / mean

# Set up the GP model
kernel = RotationKernel(
    np.log(np.var(y)), np.log(10), np.log(2.0), np.log(1.0)
)
gp = genrp.GP(kernel, mean=np.median(y))
gp.compute(t, yerr)

# Define the model
def neg_log_like(params, y, gp):
    gp.set_parameter_vector(params)
    return -gp.log_likelihood(y)

# Optimize with random restarts
initial_params = gp.get_parameter_vector()
bounds = [
    np.log(np.var(y) * np.array([0.01, 100])),
    np.log([np.max(np.diff(t)), (t.max() - t.min())]),
    np.log([3*np.median(np.diff(t)), 0.5*(t.max() - t.min())]),
    [-5.0, np.log(5.0)],
]
best = (np.inf, initial_params)
for i in range(10):
    p0 = np.array([np.random.uniform(*b) for b in bounds])
    r = minimize(neg_log_like, p0, method="L-BFGS-B", bounds=bounds,
                 args=(y, gp))
    if r.success and r.fun < best[0]:
        best = (r.fun, r.x)
        gp.set_parameter_vector(best[1])
        print("log-like: {0}, period: {1}".format(
            -r.fun, np.exp(gp.get_parameter("kernel:log_period"))
        ))
gp.set_parameter_vector(best[1])
ml_params = np.array(best[1])
y_samp = gp.sample(t)

# Do the MCMC
def log_prob(params):
    if any((p < b[0] or b[1] < p) for p, b in zip(params, bounds)):
        return -np.inf
    gp.set_parameter_vector(params)
    return gp.log_likelihood(y)

# Initialize
ndim = len(ml_params)
nwalkers = 32
pos = ml_params + 1e-5 * np.random.randn(nwalkers, ndim)
lp = np.array(list(map(log_prob, pos)))
m = ~np.isfinite(lp)
while np.any(m):
    pos[m] = ml_params + 1e-5 * np.random.randn(m.sum(), ndim)
    lp[m] = np.array(list(map(log_prob, pos[m])))
    m = ~np.isfinite(lp)

# Sample
sampler = emcee.EnsembleSampler(nwalkers, ndim, log_prob)
pos, _, _ = sampler.run_mcmc(pos, 250)
sampler.reset()
pos, _, _ = sampler.run_mcmc(pos, 1000)

# Compute the model predictions
gp.set_parameter_vector(ml_params)
x = np.linspace(t.min(), t.max(), 5000)
mu, var = gp.predict(y, x, return_var=True)
omega = np.exp(np.linspace(np.log(0.1), np.log(10), 5000))
psd = gp.kernel.get_psd(omega)
period = np.exp(gp.get_parameter("kernel:log_period"))
tau = np.linspace(0, 4*period, 5000)
acf = gp.kernel.get_value(tau)

# Compute the sample predictions
samples = sampler.flatchain
samples = samples[np.random.randint(len(samples), size=1000)]
psds = np.empty((len(samples), len(omega)))
acfs = np.empty((len(samples), len(tau)))
for i, s in enumerate(samples):
    gp.set_parameter_vector(s)
    psds[i] = gp.kernel.get_psd(omega)
    acfs[i] = gp.kernel.get_value(tau)

# Set up the figure
fig, axes = plt.subplots(2, 2, figsize=2*np.array(SQUARE_FIGSIZE))

# Plot the data
ax = axes[0, 0]
color = COLORS["MODEL_1"]
ax.errorbar(t, y, yerr=yerr, fmt=".k", capsize=0, rasterized=True)
ax.plot(x, mu, color=color)
ax.fill_between(x, mu + np.sqrt(var), mu - np.sqrt(var),
                color=color, alpha=0.3)
ax.set_xlim(t.min(), t.max())
ax.set_ylim(-1.2, 1.2)
ax.set_xlabel("time [days]")
ax.set_ylabel("relative flux [ppt]")
ax.annotate("Kepler light curve", xy=(1, 1), xycoords="axes fraction",
            ha="right", va="top", xytext=(-5, -5), textcoords="offset points",
            fontsize=12)

# Plot the PSD
ax = axes[0, 1]
f = omega / (2*np.pi)
q = np.percentile(psds, [16, 50, 84], axis=0)
ax.fill_between(f, q[0], q[2], alpha=0.5, color=color, edgecolor="none")
ax.plot(f, q[1], color=color, lw=1.5)
ax.plot(f, psd, "--k", lw=1.5)
ax.set_yscale("log")
ax.set_xscale("log")
ax.set_xlim(f[0], f[-1])
ax.set_ylim(3e-4, 5e-1)
ax.set_xlabel("$\omega\,[\mathrm{days}^{-1}]$")
ax.set_ylabel("$S(\omega)$")
ax.annotate("power spectrum", xy=(1, 1), xycoords="axes fraction",
            ha="right", va="top", xytext=(-5, -5), textcoords="offset points",
            fontsize=12)

# Plot the ACF
ax = axes[1, 1]
q = np.percentile(acfs, [16, 50, 84], axis=0)
ax.fill_between(tau, q[0], q[2], alpha=0.5, color=color, edgecolor="none")
ax.plot(tau, q[1], color=color, lw=1.5)
ax.plot(tau, acf, "--k", lw=1.5)
ax.set_xlim(tau[0], tau[-1])
ax.set_ylim(0, 0.155)
ax.set_xlabel(r"$\tau\,[\mathrm{days}]$")
ax.set_ylabel(r"$k(\tau)$")
ax.annotate("covariance function", xy=(1, 1), xycoords="axes fraction",
            ha="right", va="top", xytext=(-5, -5), textcoords="offset points",
            fontsize=12)

# Plot a sample
ax = axes[1, 0]
ax.plot(t, y_samp, ".k", rasterized=True)
ax.set_xlim(t.min(), t.max())
ax.set_ylim(-1.2, 1.2)
ax.set_xlabel("time [days]")
ax.set_ylabel("relative flux [ppt]")
ax.annotate("simulated light curve", xy=(1, 1), xycoords="axes fraction",
            ha="right", va="top", xytext=(-5, -5), textcoords="offset points",
            fontsize=12)

fig.savefig("rotation.pdf", bbox_inches="tight", dpi=300)
plt.close(fig)

# Plot the period constraint
period_samps = np.exp(sampler.flatchain[:, 2])
fig, ax = plt.subplots(1, 1, figsize=SQUARE_FIGSIZE)
ax.hist(period_samps, 40, histtype="step", color=color)
ax.yaxis.set_major_locator(plt.NullLocator())
mu, std = np.mean(period_samps), np.std(period_samps)
ax.set_xlim(mu - 3.5*std, mu + 3.5*std)
ax.set_xlabel("rotation period [days]")
fig.savefig("rotation-period.pdf", bbox_inches="tight", dpi=300)

with open("rotation.tex", "w") as f:
    f.write("% Automatically generated\n")
    f.write(("\\newcommand{{\\rotationperiod}}{{\\ensuremath{{{{"
             "{0:.2f} \pm {1:.2f} }}}}}}\n").format(mu, std))
