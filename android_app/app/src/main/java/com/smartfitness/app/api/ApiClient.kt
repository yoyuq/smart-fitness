package com.smartfitness.app.api

import android.content.Context
import android.content.SharedPreferences
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object ApiClient {

    private const val DEFAULT_BASE_URL_EMULATOR = "http://10.0.2.2:8080/"
    /** 真机默认服务器（主机在 192.168.123.203 上跑 :8080） */
    const val DEFAULT_BASE_URL_REAL = "http://192.168.123.203:8080/"

    /** 运行时看是否在模拟器上（Build.FINGERPRINT 含 generic/sdk）选默认 */
    private fun isProbablyEmulator(): Boolean {
        val fp = android.os.Build.FINGERPRINT.lowercase()
        return fp.contains("generic") || fp.contains("sdk_gphone") || fp.contains("emulator")
    }

    /** 动态读取 SharedPrefs.base_url; 没设过 → 根据设备默认 */
    val BASE_URL: String
        get() {
            val override = prefs?.getString("base_url", null)
            if (!override.isNullOrBlank()) return if (override.endsWith("/")) override else "$override/"
            return if (isProbablyEmulator()) DEFAULT_BASE_URL_EMULATOR else DEFAULT_BASE_URL_REAL
        }
    private const val PREFS_NAME = "smart_fitness_prefs"
    private const val KEY_TOKEN = "auth_token"
    private const val KEY_USER_ID = "user_id"
    private const val KEY_USERNAME = "username"
    private const val KEY_DEVICE_ID = "device_id"

    @Volatile
    private var prefs: SharedPreferences? = null

    fun init(context: Context) {
        if (prefs == null) {
            synchronized(this) {
                if (prefs == null) {
                    prefs = context.applicationContext.getSharedPreferences(
                        PREFS_NAME,
                        Context.MODE_PRIVATE
                    )
                }
            }
        }
    }

    private fun requirePrefs(): SharedPreferences =
        prefs ?: throw IllegalStateException("ApiClient.init(context) must be called first")

    // ---------- Token / user state ----------

    var token: String?
        get() = requirePrefs().getString(KEY_TOKEN, null)
        set(value) {
            requirePrefs().edit().apply {
                if (value == null) remove(KEY_TOKEN) else putString(KEY_TOKEN, value)
            }.apply()
        }

    var userId: Long
        get() = requirePrefs().getLong(KEY_USER_ID, -1L)
        set(value) {
            requirePrefs().edit().putLong(KEY_USER_ID, value).apply()
        }

    var username: String?
        get() = requirePrefs().getString(KEY_USERNAME, null)
        set(value) {
            requirePrefs().edit().apply {
                if (value == null) remove(KEY_USERNAME) else putString(KEY_USERNAME, value)
            }.apply()
        }

    fun getOrCreateDeviceId(): String {
        val p = requirePrefs()
        var id = p.getString(KEY_DEVICE_ID, null)
        if (id == null) {
            id = "android-" + java.util.UUID.randomUUID().toString().take(12)
            p.edit().putString(KEY_DEVICE_ID, id).apply()
        }
        return id
    }

    fun clearAuth() {
        requirePrefs().edit()
            .remove(KEY_TOKEN)
            .remove(KEY_USER_ID)
            .remove(KEY_USERNAME)
            .apply()
    }

    fun isLoggedIn(): Boolean = !token.isNullOrEmpty()

    // ---------- HTTP / Retrofit ----------

    private val authInterceptor = okhttp3.Interceptor { chain ->
        val original = chain.request()
        val builder = original.newBuilder()
        token?.let { builder.header("Authorization", "Bearer $it") }
        chain.proceed(builder.build())
    }

    val okHttpClient: OkHttpClient by lazy {
        val logger = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BASIC
        }
        OkHttpClient.Builder()
            .addInterceptor(authInterceptor)
            .addInterceptor(logger)
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .pingInterval(20, TimeUnit.SECONDS)
            .build()
    }

    @Volatile private var _service: ApiService? = null

    val service: ApiService
        get() = _service ?: synchronized(this) {
            _service ?: buildService().also { _service = it }
        }

    private fun buildService(): ApiService = Retrofit.Builder()
        .baseUrl(BASE_URL)
        .client(okHttpClient)
        .addConverterFactory(GsonConverterFactory.create())
        .build()
        .create(ApiService::class.java)

    /** 设置/清除服务器地址，下一次 .service 累的 baseUrl 会走新值。为了避免践踏现有在跨递 token，调用后最好重启 Activity。 */
    fun setBaseUrl(url: String?) {
        prefs?.edit()?.apply {
            if (url.isNullOrBlank()) remove("base_url") else putString("base_url", url)
            apply()
        }
        _service = null  // 下次 service 会重建
    }
}
