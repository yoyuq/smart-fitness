package com.smartfitness.app

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.fragment.app.FragmentContainerView
import androidx.navigation.NavController
import androidx.navigation.NavOptions
import androidx.navigation.fragment.NavHostFragment
import androidx.navigation.ui.setupWithNavController
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.smartfitness.app.api.ApiClient
import com.smartfitness.app.api.NetworkMonitor
import com.smartfitness.app.api.OfflineQueue
import com.smartfitness.app.push.PushChannel

class MainActivity : AppCompatActivity() {

    private lateinit var navController: NavController
    private lateinit var bottomNav: BottomNavigationView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ApiClient.init(applicationContext)
        OfflineQueue.init(applicationContext)
        NetworkMonitor.start(applicationContext)
        PushChannel.ensureChannel(applicationContext)
        // 启动时试一次重放 (上次退出后可能遗留未发项)
        OfflineQueue.tryFlush()
        setContentView(R.layout.activity_main)

        val navHostFragment = supportFragmentManager
            .findFragmentById(R.id.nav_host_fragment) as NavHostFragment
        navController = navHostFragment.navController
        bottomNav = findViewById(R.id.bottom_nav)
        bottomNav.setupWithNavController(navController)

        // 已登录时跳到 home, 否则停在 login(startDestination)
        if (ApiClient.isLoggedIn()) {
            val opts = NavOptions.Builder()
                .setPopUpTo(R.id.loginFragment, true)
                .build()
            navController.navigate(R.id.homeFragment, null, opts)
        }

        // 登录/注册后自动跳 home → 显示底部导航；退到 login → 隐藏
        navController.addOnDestinationChangedListener { _, dest, _ ->
            when (dest.id) {
                R.id.loginFragment -> bottomNav.visibility = android.view.View.GONE
                else -> bottomNav.visibility = android.view.View.VISIBLE
            }
        }
    }
}
