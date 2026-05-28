package com.smartfitness.app.ui.login

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.navigation.NavOptions
import androidx.navigation.fragment.findNavController
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText
import com.smartfitness.app.R
import com.smartfitness.app.api.ApiClient
import com.smartfitness.app.model.LoginRequest
import com.smartfitness.app.model.RegisterRequest
import kotlinx.coroutines.launch

class LoginFragment : Fragment() {

    private var isRegisterMode = false

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_login, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        val usernameInput = view.findViewById<TextInputEditText>(R.id.input_username)
        val passwordInput = view.findViewById<TextInputEditText>(R.id.input_password)
        val submitButton = view.findViewById<MaterialButton>(R.id.btn_submit)
        val toggleButton = view.findViewById<MaterialButton>(R.id.btn_toggle_mode)

        val updateMode = {
            if (isRegisterMode) {
                submitButton.text = getString(R.string.register)
                toggleButton.text = getString(R.string.have_account_login)
            } else {
                submitButton.text = getString(R.string.login)
                toggleButton.text = getString(R.string.no_account_register)
            }
        }
        updateMode()

        toggleButton.setOnClickListener {
            isRegisterMode = !isRegisterMode
            updateMode()
        }

        submitButton.setOnClickListener {
            val u = usernameInput.text?.toString()?.trim().orEmpty()
            val p = passwordInput.text?.toString()?.trim().orEmpty()
            if (u.isEmpty() || p.isEmpty()) {
                Toast.makeText(requireContext(), "Please fill all fields", Toast.LENGTH_SHORT)
                    .show()
                return@setOnClickListener
            }
            submitButton.isEnabled = false
            // 用 viewLifecycleOwner 保证 fragment view 销毁后协程被取消
            viewLifecycleOwner.lifecycleScope.launch {
                try {
                    if (isRegisterMode) {
                        val resp = ApiClient.service.register(
                            RegisterRequest(u, p, ApiClient.getOrCreateDeviceId())
                        )
                        if (resp.ok && !resp.token.isNullOrEmpty()) {
                            ApiClient.token = resp.token
                            ApiClient.userId = resp.userId ?: -1L
                            ApiClient.username = resp.username ?: u
                            if (!isAdded) return@launch
                            Toast.makeText(
                                requireContext(),
                                "Registered & logged in",
                                Toast.LENGTH_SHORT
                            ).show()
                            // Task 3: 新用户初次引导
                            if (!OnboardingHelper.isCompleted(requireContext())) {
                                OnboardingHelper.show(requireContext(), viewLifecycleOwner.lifecycleScope) {
                                    if (isAdded) findNavController().navigate(R.id.homeFragment, null, NavOptions.Builder().setPopUpTo(R.id.loginFragment, true).build())
                                }
                            } else {
                                findNavController().navigate(R.id.homeFragment, null, NavOptions.Builder().setPopUpTo(R.id.loginFragment, true).build())
                            }
                        } else {
                            if (!isAdded) return@launch
                            Toast.makeText(
                                requireContext(),
                                resp.message ?: "Register failed",
                                Toast.LENGTH_SHORT
                            ).show()
                        }
                    } else {
                        val resp = ApiClient.service.login(LoginRequest(u, p))
                        if (resp.ok && !resp.token.isNullOrEmpty()) {
                            ApiClient.token = resp.token
                            ApiClient.userId = resp.userId ?: -1L
                            ApiClient.username = resp.username ?: u
                            if (!isAdded) return@launch
                            Toast.makeText(requireContext(), "Welcome!", Toast.LENGTH_SHORT).show()
                            findNavController().navigate(R.id.homeFragment, null, NavOptions.Builder().setPopUpTo(R.id.loginFragment, true).build())
                        } else {
                            if (!isAdded) return@launch
                            Toast.makeText(
                                requireContext(),
                                resp.message ?: "Login failed",
                                Toast.LENGTH_SHORT
                            ).show()
                        }
                    }
                } catch (e: Exception) {
                    if (!isAdded) return@launch
                    Toast.makeText(
                        requireContext(),
                        "Network error: ${e.message}",
                        Toast.LENGTH_LONG
                    ).show()
                } finally {
                    if (isAdded) submitButton.isEnabled = true
                }
            }
        }
    }
}
